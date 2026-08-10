from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .parser import _ean13_is_valid, normalized, parse_structured_item


@dataclass(frozen=True)
class OCRToken:
    text: str
    confidence: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


_OCR_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _recognize(image: np.ndarray, *, detect: bool = True):
    with _OCR_LOCK:
        return _engine()(image, use_det=detect, use_cls=False, use_rec=True)[0] or []


def _load_image(path: str | Path) -> np.ndarray:
    pil_image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR)


def _tokens(image: np.ndarray) -> list[OCRToken]:
    height, width = image.shape[:2]
    scale = min(1.0, 2200 / width)
    detected = (
        cv2.resize(image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA)
        if scale < 1
        else image
    )
    result = _recognize(detected)
    tokens = []
    for box, text, confidence in result:
        points = np.asarray(box, dtype=float)
        tokens.append(
            OCRToken(
                text=str(text).strip(),
                confidence=float(confidence),
                x0=float(points[:, 0].min()),
                y0=float(points[:, 1].min()),
                x1=float(points[:, 0].max()),
                y1=float(points[:, 1].max()),
            )
        )
    return tokens, scale


def _grouped_text(tokens: list[OCRToken]) -> str:
    if not tokens:
        return ""
    ordered = sorted(tokens, key=lambda token: token.cy)
    rows: list[list[OCRToken]] = []
    for token in ordered:
        if not rows or abs(token.cy - np.median([part.cy for part in rows[-1]])) > 12:
            rows.append([token])
        else:
            rows[-1].append(token)
    return "\n".join(
        " ".join(token.text for token in sorted(row, key=lambda token: token.x0))
        for row in rows
    )


def _plain(value: str) -> str:
    return normalized(value).lower()


def _reported_count(tokens: list[OCRToken]) -> tuple[int | None, float | None]:
    for label in tokens:
        text = _plain(label.text)
        if "itens" not in text or ("qtd" not in text and "total" not in text):
            continue
        candidates = [
            token
            for token in tokens
            if token.x0 >= label.x1 - 20
            and abs(token.cy - label.cy) <= 30
            and re.fullmatch(r"\D*(\d{1,3})\D*", token.text)
        ]
        if candidates:
            selected = min(candidates, key=lambda token: abs(token.cy - label.cy))
            return int(re.search(r"\d{1,3}", selected.text).group()), label.cy
    return None, None


def _table_bounds(tokens: list[OCRToken], footer_y: float | None) -> tuple[float, float]:
    header_candidates = [
        token.cy
        for token in tokens
        if "descricao" in _plain(token.text) or re.search(r"#\s*cod", _plain(token.text))
    ]
    if not header_candidates or footer_y is None:
        raise ValueError("Não foi possível localizar o cabeçalho e o rodapé da lista de itens.")
    return min(header_candidates), footer_y


def _row_geometry(
    tokens: list[OCRToken], header_y: float, footer_y: float, count: int
) -> tuple[float, float, float, float]:
    table_tokens = [token for token in tokens if header_y + 15 < token.cy < footer_y - 15]
    code_rows = sorted(
        token.cy for token in table_tokens if re.search(r"\d{4,14}", token.text)
    )
    if not code_rows:
        raise ValueError("Nenhum código de produto foi localizado na tabela.")

    clustered: list[float] = []
    for y in code_rows:
        if not clustered or y - clustered[-1] > 8:
            clustered.append(y)
        else:
            clustered[-1] = (clustered[-1] + y) / 2

    first = clustered[0]
    initial_step = (footer_y - first) / (count + 1)
    for _ in range(2):
        indexed = []
        for y in clustered:
            index = round((y - first) / initial_step)
            if 0 <= index < count and abs(y - (first + index * initial_step)) < initial_step * 0.45:
                indexed.append((index, y))
        if len(indexed) >= 4:
            coefficients = np.polyfit(
                [index for index, _y in indexed], [y for _index, y in indexed], 1
            )
            initial_step, first = float(coefficients[0]), float(coefficients[1])

    x0 = max(0.0, min(token.x0 for token in table_tokens) - 25)
    x1 = max(token.x1 for token in table_tokens) + 25
    return first, initial_step, x0, x1


def _recognize_row(crop: np.ndarray, line_number: int) -> tuple[str, float, dict | None]:
    enlarged = cv2.resize(crop, None, fx=1.35, fy=1.35, interpolation=cv2.INTER_CUBIC)
    attempts = [crop, enlarged]
    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
    attempts.append(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1])

    best = ("", 0.0, None)
    for candidate in attempts:
        result = _recognize(candidate, detect=False)
        if not result:
            continue
        text, confidence = result[0]
        item = parse_structured_item(str(text), line_number, float(confidence))
        current = (str(text), float(confidence), item)
        if item is not None:
            return current
        if current[1] > best[1]:
            best = current
    return best


def _description_key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalized(value).upper())


def _harmonize_repeated_items(items: list[dict]) -> None:
    """Usa repetições do mesmo produto como votação para código e descrição."""

    start = 0
    while start < len(items):
        group = [items[start]]
        end = start + 1
        while end < len(items):
            previous, candidate = group[-1], items[end]
            same_code = previous["item_code"] and previous["item_code"] == candidate["item_code"]
            one_valid_code = _ean13_is_valid(previous["item_code"]) != _ean13_is_valid(candidate["item_code"])
            similar = SequenceMatcher(
                None,
                _description_key(previous["description"]),
                _description_key(candidate["description"]),
            ).ratio() >= 0.55
            same_price = previous["item_total"] == candidate["item_total"]
            if not (same_code or (one_valid_code and similar and same_price)):
                break
            group.append(candidate)
            end += 1

        if len(group) > 1:
            valid_codes = [item["item_code"] for item in group if _ean13_is_valid(item["item_code"])]
            if valid_codes:
                selected_code = max(set(valid_codes), key=valid_codes.count)
                for item in group:
                    item["item_code"] = selected_code

            def description_score(item: dict) -> float:
                key = _description_key(item["description"])
                consensus = sum(
                    SequenceMatcher(None, key, _description_key(other["description"])).ratio()
                    for other in group
                )
                suspicious = len(re.findall(r"[A-Z][0-9]|[0-9][A-Z]", item["description"]))
                return consensus + item["confidence"] - suspicious * 0.08

            selected_description = max(group, key=description_score)["description"]
            for item in group:
                item["description"] = selected_description
            if len({item["item_code"] for item in group}) == 1:
                measures = [
                    (item["quantity"], item["unit"], item["unit_price"], item["item_total"])
                    for item in group
                ]
                selected_measure = max(
                    set(measures),
                    key=lambda measure: (
                        measures.count(measure),
                        -abs(measure[0] * measure[2] - measure[3]),
                    ),
                )
                for item in group:
                    item["quantity"], item["unit"], item["unit_price"], item["item_total"] = selected_measure
                    item["gross_total"] = item["quantity"] * item["unit_price"]
                    item["discount"] = max(0.0, item["gross_total"] - item["item_total"])
                    if _ean13_is_valid(item["item_code"]):
                        item["confidence"] = max(item["confidence"], 0.85)
        start = end


def extract_structured_receipt(path: str | Path) -> dict:
    """Extrai a tabela usando a posição fixa das seis colunas do cupom."""

    image = _load_image(path)
    tokens, detection_scale = _tokens(image)
    raw_text = _grouped_text(tokens)
    count, footer_y = _reported_count(tokens)
    if not count or not footer_y or not 1 <= count <= 250:
        raise ValueError("A quantidade total de itens não foi reconhecida.")

    header_y, footer_y = _table_bounds(tokens, footer_y)
    first, step, x0, x1 = _row_geometry(tokens, header_y, footer_y, count)
    inverse = 1 / detection_scale
    x0_px = max(0, round(x0 * inverse))
    x1_px = min(image.shape[1], round(x1 * inverse))
    half_height = max(20, round(step * inverse * 0.47))

    items = []
    row_texts = []
    for index in range(count):
        center = round((first + index * step) * inverse)
        y0 = max(0, center - half_height)
        y1 = min(image.shape[0], center + half_height)
        text, confidence, item = _recognize_row(image[y0:y1, x0_px:x1_px], index + 1)
        row_texts.append(f"{index + 1:02d}: {text}")
        if item is None:
            item = {
                "line_number": index + 1,
                "item_code": "",
                "description": f"Item {index + 1} — revisar OCR",
                "quantity": 1.0,
                "unit": "UN",
                "unit_price": 0.0,
                "gross_total": 0.0,
                "discount": 0.0,
                "item_total": 0.0,
                "category": "Outros",
                "confidence": 0.0,
                "extraction_source": "rapidocr",
                "uncertain_fields": ["item_code", "description", "quantity", "unit", "unit_price", "item_total"],
                "ocr_text": text,
            }
        else:
            item["extraction_source"] = "rapidocr"
            item["uncertain_fields"] = (
                ["description"] if item.get("confidence", 0) < 0.75 else []
            )
        # Coordenadas internas usadas para enviar somente as linhas duvidosas
        # ao modelo visual no modo hibrido. Elas nao sao persistidas no banco.
        item["_crop_box"] = [x0_px, y0, x1_px, y1]
        items.append(item)

    _harmonize_repeated_items(items)

    return {
        "raw_text": raw_text + "\n\n--- LINHAS RECONHECIDAS INDIVIDUALMENTE ---\n" + "\n".join(row_texts),
        "reported_item_count": count,
        "items": items,
    }
