from __future__ import annotations

from pathlib import Path
from typing import Mapping

import cv2
import numpy as np
import pytesseract
import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .llm_ocr import (
    LLMOCRConfigurationError,
    LLMOCRResponseError,
    extract_with_llm,
    validated_llm_items,
)
from .parser import parse_receipt_text
from .structured_ocr import extract_structured_receipt


def preprocess_receipt(path: str | Path) -> Image.Image:
    image = Image.open(path)
    image = ImageOps.exif_transpose(image).convert("L")
    max_width = 2200
    if image.width > max_width:
        ratio = max_width / image.width
        image = image.resize((max_width, int(image.height * ratio)))
    image = ImageEnhance.Contrast(image).enhance(1.8)
    image = ImageEnhance.Sharpness(image).enhance(1.5)
    image = image.filter(ImageFilter.MedianFilter(size=3))
    return image


def extract_text(path: str | Path, language: str = "por") -> str:
    image = preprocess_receipt(path)
    attempts = []
    for psm in (6, 4):
        try:
            attempts.append(
                pytesseract.image_to_string(
                    image, lang=language, config=f"--oem 3 --psm {psm}"
                )
            )
        except pytesseract.TesseractError:
            attempts.append(
                pytesseract.image_to_string(
                    image, lang="eng", config=f"--oem 3 --psm {psm}"
                )
            )
    return max(attempts, key=lambda value: len(value.strip()), default="")


def decode_qr(path: str | Path) -> str | None:
    image = cv2.imread(str(path))
    if image is None:
        return None
    detector = cv2.QRCodeDetector()
    height, width = image.shape[:2]
    variants = [image]
    if max(height, width) > 1800:
        scale = 1800 / max(height, width)
        variants.append(
            cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        )
    gray = cv2.cvtColor(variants[-1], cv2.COLOR_BGR2GRAY)
    variants.extend(
        [
            gray,
            cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
            cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 7
            ),
        ]
    )
    for candidate in variants:
        try:
            ok, decoded, _points, _ = detector.detectAndDecodeMulti(candidate)
            if ok:
                for value in decoded:
                    if value:
                        return value.strip()
        except (cv2.error, ValueError):
            pass
        value, _points, _ = detector.detectAndDecode(candidate)
        if value:
            return value.strip()
        try:
            value, _points, _ = detector.detectAndDecodeCurved(candidate)
            if value:
                return value.strip()
        except cv2.error:
            pass
    return None


def _local_process(path: str | Path, language: str) -> dict:
    try:
        structured = extract_structured_receipt(path)
        raw_text = structured["raw_text"]
        parsed = parse_receipt_text(raw_text)
        parsed["reported_item_count"] = structured["reported_item_count"]
        parsed["items"] = structured["items"]
        if all(item.get("item_total", 0) > 0 for item in structured["items"]):
            parsed["subtotal"] = sum(item["gross_total"] for item in structured["items"])
            parsed["discount_total"] = sum(item["discount"] for item in structured["items"])
            parsed["total_paid"] = sum(item["item_total"] for item in structured["items"])
        parsed["ocr_method"] = "rapidocr"
    except (ImportError, OSError, RuntimeError, ValueError, cv2.error):
        raw_text = extract_text(path, language)
        parsed = parse_receipt_text(raw_text)
        parsed["ocr_method"] = "tesseract_fallback"
        for item in parsed.get("items", []):
            item["extraction_source"] = "tesseract"
            item["uncertain_fields"] = []
    parsed["raw_text"] = raw_text
    parsed["ocr_warnings"] = []
    return parsed


def _target_lines(parsed: dict, threshold: float) -> list[int]:
    targets = []
    for item in parsed.get("items", []):
        expected = float(item.get("gross_total", 0)) - float(item.get("discount", 0))
        if (
            not item.get("item_code")
            or item.get("confidence") is None
            or float(item.get("confidence", 0)) < threshold
            or abs(expected - float(item.get("item_total", 0))) > 0.08
        ):
            targets.append(int(item.get("line_number") or len(targets) + 1))
    return sorted(set(targets))


def _merge_metadata(parsed: dict, llm_result: dict, *, replace: bool) -> None:
    for key in [
        "merchant_name",
        "merchant_cnpj",
        "merchant_address",
        "purchased_at",
        "reported_item_count",
        "subtotal",
        "discount_total",
        "total_paid",
        "payment_method",
        "receipt_number",
        "series",
        "access_key",
        "qr_url",
    ]:
        value = llm_result.get(key)
        if value not in (None, "", 0) and (replace or parsed.get(key) in (None, "", 0)):
            parsed[key] = value


def _merge_hybrid(parsed: dict, llm_result: dict, targets: list[int]) -> None:
    replacements = {
        item["line_number"]: item
        for item in validated_llm_items(llm_result, set(targets))
        if item.get("confidence", 0) >= 0.55
    }
    parsed["items"] = [
        replacements.get(int(item.get("line_number", 0)), item)
        for item in parsed.get("items", [])
    ]
    missing = sorted(set(targets) - set(replacements))
    if missing:
        parsed["ocr_warnings"].append(
            "A LLM não produziu uma correção válida para as linhas: "
            + ", ".join(map(str, missing))
        )
    _merge_metadata(parsed, llm_result, replace=False)


def _replace_with_llm(parsed: dict, llm_result: dict) -> None:
    items = validated_llm_items(llm_result)
    expected = int(llm_result.get("reported_item_count") or parsed.get("reported_item_count") or 0)
    actual_lines = {item["line_number"] for item in items}
    if not items or (expected and actual_lines != set(range(1, expected + 1))):
        raise LLMOCRResponseError("A LLM não retornou todas as linhas esperadas")
    parsed["items"] = items
    _merge_metadata(parsed, llm_result, replace=True)


def _reconcile_totals(parsed: dict) -> None:
    items = parsed.get("items", [])
    if not items or any(float(item.get("item_total", 0)) <= 0 for item in items):
        return
    parsed["subtotal"] = round(sum(float(item.get("gross_total", 0)) for item in items), 2)
    parsed["discount_total"] = round(sum(float(item.get("discount", 0)) for item in items), 2)
    parsed["total_paid"] = round(sum(float(item.get("item_total", 0)) for item in items), 2)


def process_image(
    path: str | Path,
    language: str = "por",
    mode: str = "hybrid",
    llm_settings: Mapping | None = None,
) -> dict:
    settings = llm_settings or {}
    mode = (mode or "hybrid").lower()
    if mode not in {"rapidocr", "hybrid", "openai", "ollama"}:
        mode = "hybrid"
    parsed = _local_process(path, language)
    threshold = float(settings.get("OCR_CONFIDENCE_THRESHOLD", 0.75))
    targets = _target_lines(parsed, threshold)

    provider = str(settings.get("LLM_PROVIDER", "openai")).lower() if mode == "hybrid" else mode
    should_call_llm = mode in {"openai", "ollama"} or (mode == "hybrid" and bool(targets))
    if should_call_llm:
        try:
            llm_result = extract_with_llm(
                path,
                provider,
                settings,
                local_result=parsed,
                target_lines=targets if mode == "hybrid" else None,
            )
            if mode == "hybrid":
                _merge_hybrid(parsed, llm_result, targets)
                parsed["ocr_method"] = f"hybrid:{provider}"
            else:
                _replace_with_llm(parsed, llm_result)
                parsed["ocr_method"] = provider
        except (
            LLMOCRConfigurationError,
            LLMOCRResponseError,
            requests.RequestException,
            OSError,
            ValueError,
        ) as error:
            parsed["ocr_warnings"].append(
                f"{provider.capitalize()} indisponível; mantido o OCR local: {error}"
            )

    _reconcile_totals(parsed)
    parsed["qr_url"] = decode_qr(path) or parsed.get("qr_url")
    return parsed
