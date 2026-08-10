from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

from ..categories import infer_category


UNITS = {"UN", "KG", "PT", "CX", "PC", "PCT", "FD", "LT", "L", "G", "ML"}

ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "line_number": {"type": "integer"},
        "item_code": {"type": "string"},
        "description": {"type": "string"},
        "quantity": {"type": "number"},
        "unit": {"type": "string"},
        "unit_price": {"type": "number"},
        "gross_total": {"type": "number"},
        "discount": {"type": "number"},
        "item_total": {"type": "number"},
        "confidence": {"type": "number"},
        "uncertain_fields": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "string"},
    },
    "required": [
        "line_number",
        "item_code",
        "description",
        "quantity",
        "unit",
        "unit_price",
        "gross_total",
        "discount",
        "item_total",
        "confidence",
        "uncertain_fields",
        "evidence",
    ],
}

RECEIPT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "merchant_name": {"type": "string"},
        "merchant_cnpj": {"type": "string"},
        "merchant_address": {"type": "string"},
        "purchased_at": {"type": "string"},
        "reported_item_count": {"type": "integer"},
        "subtotal": {"type": "number"},
        "discount_total": {"type": "number"},
        "total_paid": {"type": "number"},
        "payment_method": {"type": "string"},
        "receipt_number": {"type": "string"},
        "series": {"type": "string"},
        "access_key": {"type": "string"},
        "qr_url": {"type": "string"},
        "items": {"type": "array", "items": ITEM_SCHEMA},
    },
    "required": [
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
        "items",
    ],
}


class LLMOCRConfigurationError(RuntimeError):
    pass


class LLMOCRResponseError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str
    model: str
    base_url: str
    api_key: str
    timeout: int = 90
    image_detail: str = "high"


def provider_config(provider: str, settings: Mapping) -> LLMProviderConfig:
    provider = provider.lower()
    timeout = int(settings.get("LLM_TIMEOUT_SECONDS", 90))
    detail = str(settings.get("LLM_IMAGE_DETAIL", "high"))
    if provider == "openai":
        api_key = str(settings.get("OPENAI_API_KEY", "")).strip()
        if not api_key:
            raise LLMOCRConfigurationError("OPENAI_API_KEY não configurada")
        return LLMProviderConfig(
            provider="openai",
            model=str(settings.get("OPENAI_MODEL", "gpt-5.6-terra")),
            base_url=str(settings.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/"),
            api_key=api_key,
            timeout=timeout,
            image_detail=detail,
        )
    if provider == "ollama":
        return LLMProviderConfig(
            provider="ollama",
            model=str(settings.get("OLLAMA_MODEL", "qwen3-vl:8b")),
            base_url=str(settings.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434/v1")).rstrip("/"),
            api_key=str(settings.get("OLLAMA_API_KEY", "ollama")),
            timeout=timeout,
            image_detail=detail,
        )
    raise LLMOCRConfigurationError(f"Provedor de LLM desconhecido: {provider}")


def _target_contact_sheet(
    image: Image.Image,
    local_result: dict | None,
    target_lines: list[int] | None,
) -> Image.Image | None:
    if not local_result or not target_lines:
        return None
    items = {
        int(item.get("line_number", 0)): item
        for item in local_result.get("items", [])
    }
    panels: list[Image.Image] = []
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 30)
    except OSError:
        font = ImageFont.load_default()

    for line_number in target_lines:
        box = items.get(line_number, {}).get("_crop_box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        x0, y0, x1, y1 = (int(value) for value in box)
        row_height = max(1, y1 - y0)
        x0 = max(0, x0 - 20)
        x1 = min(image.width, x1 + 20)
        y0 = max(0, y0 - max(8, row_height // 4))
        y1 = min(image.height, y1 + max(8, row_height // 4))
        if x1 <= x0 or y1 <= y0:
            return None
        crop = image.crop((x0, y0, x1, y1))
        panel = Image.new("RGB", (crop.width, crop.height + 48), "white")
        ImageDraw.Draw(panel).text(
            (10, 7), f"LINHA {line_number:02d}", fill="black", font=font
        )
        panel.paste(crop, (0, 48))
        panels.append(panel)

    if not panels:
        return None
    width = max(panel.width for panel in panels)
    height = sum(panel.height for panel in panels) + 12 * (len(panels) - 1)
    sheet = Image.new("RGB", (width, height), "white")
    y = 0
    for panel in panels:
        sheet.paste(panel, (0, y))
        y += panel.height + 12
    sheet.thumbnail((2200, 2400), Image.Resampling.LANCZOS)
    return sheet


def _image_data_url(
    path: str | Path,
    local_result: dict | None = None,
    target_lines: list[int] | None = None,
) -> str:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        contact_sheet = _target_contact_sheet(image, local_result, target_lines)
        image = contact_sheet or image
        if contact_sheet is None:
            image.thumbnail((2200, 3200), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=85, optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _prompt(local_result: dict | None, target_lines: list[int] | None) -> str:
    targets = (
        "Extraia SOMENTE as linhas "
        + ", ".join(map(str, target_lines))
        + ". Os recortes aparecem nessa mesma ordem e cada um possui o rotulo LINHA NN."
        if target_lines
        else "Extraia todas as linhas, exatamente uma vez e na ordem impressa."
    )
    evidence = ""
    if local_result:
        compact = {
            "reported_item_count": local_result.get("reported_item_count"),
            "merchant_cnpj": local_result.get("merchant_cnpj"),
            "total_paid": local_result.get("total_paid"),
            "items": [
                {
                    "line_number": item.get("line_number"),
                    "item_code": item.get("item_code"),
                    "description": item.get("description"),
                    "quantity": item.get("quantity"),
                    "unit": item.get("unit"),
                    "unit_price": item.get("unit_price"),
                    "item_total": item.get("item_total"),
                    "confidence": item.get("confidence"),
                    "ocr_text": item.get("ocr_text", ""),
                }
                for item in local_result.get("items", [])
                if not target_lines or item.get("line_number") in target_lines
            ],
        }
        evidence = "\nResultado preliminar do OCR local, apenas como evidência:\n" + json.dumps(
            compact, ensure_ascii=False, separators=(",", ":")
        )
    return f"""Leia este cupom fiscal brasileiro. As colunas da tabela são fixas:
#, Cod, Descrição, Qt, Un, Vlr e Total.
O número # é a ordem da linha. Cod pode ser um código de barras ou um PLU interno de 3 dígitos.
Não invente caracteres ou valores. Use string vazia/zero e liste o campo em uncertain_fields quando não houver evidência visual suficiente.
Separe valores colados usando a posição das colunas e valide gross_total - discount = item_total.
Preserve exatamente o código impresso. Datas devem usar ISO 8601 quando legíveis.
{targets}{evidence}"""


def _response_text(data: dict, provider: str) -> str:
    if provider == "ollama":
        content = data.get("message", {}).get("content")
        if isinstance(content, str) and content.strip():
            return content
    else:
        for output in data.get("output", []):
            if output.get("type") != "message":
                continue
            for content in output.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return content["text"]
    raise LLMOCRResponseError("O provedor não retornou conteúdo estruturado")


def _request_openai(config: LLMProviderConfig, prompt: str, image_url: str) -> dict:
    payload = {
        "model": config.model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": image_url,
                        "detail": config.image_detail,
                    },
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "receipt_extraction",
                "strict": True,
                "schema": RECEIPT_SCHEMA,
            }
        },
        "max_output_tokens": 10000,
    }
    response = requests.post(
        config.base_url + "/responses",
        headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=config.timeout,
    )
    response.raise_for_status()
    return response.json()


def _request_ollama(
    config: LLMProviderConfig,
    prompt: str,
    image_url: str,
    max_output_tokens: int,
) -> dict:
    base_url = config.base_url[:-3] if config.base_url.endswith("/v1") else config.base_url
    payload = {
        "model": config.model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [image_url.split(",", 1)[1]],
            }
        ],
        "format": RECEIPT_SCHEMA,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_predict": max_output_tokens},
    }
    response = requests.post(
        base_url + "/api/chat",
        headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=config.timeout,
    )
    response.raise_for_status()
    return response.json()


def extract_with_llm(
    path: str | Path,
    provider: str,
    settings: Mapping,
    local_result: dict | None = None,
    target_lines: list[int] | None = None,
) -> dict:
    config = provider_config(provider, settings)
    prompt = _prompt(local_result, target_lines)
    image_url = _image_data_url(path, local_result, target_lines)
    max_output_tokens = min(
        10000, max(1800, (len(target_lines) if target_lines else 32) * 320)
    )
    response = (
        _request_openai(config, prompt, image_url)
        if config.provider == "openai"
        else _request_ollama(config, prompt, image_url, max_output_tokens)
    )
    try:
        parsed = json.loads(_response_text(response, config.provider))
    except (TypeError, json.JSONDecodeError) as error:
        raise LLMOCRResponseError("A resposta do modelo não contém JSON válido") from error
    if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
        raise LLMOCRResponseError("A resposta do modelo não contém uma lista de itens")
    parsed["provider"] = config.provider
    parsed["model"] = config.model
    return parsed


def validated_llm_item(value: dict) -> dict | None:
    try:
        line_number = int(value.get("line_number"))
        code = re.sub(r"\D", "", str(value.get("item_code", "")))
        description = " ".join(str(value.get("description", "")).split())
        quantity = float(value.get("quantity", 0))
        unit = str(value.get("unit", "")).upper().strip()
        unit_price = float(value.get("unit_price", 0))
        item_total = float(value.get("item_total", 0))
        gross_total = float(value.get("gross_total", quantity * unit_price))
        discount = float(value.get("discount", max(0, gross_total - item_total)))
        confidence = max(0.0, min(1.0, float(value.get("confidence", 0))))
    except (TypeError, ValueError):
        return None
    if line_number < 1 or not 3 <= len(code) <= 14 or not description:
        return None
    if unit not in UNITS or quantity <= 0 or unit_price < 0 or item_total <= 0:
        return None
    uncertain = [str(field) for field in value.get("uncertain_fields", [])]
    if abs((gross_total - discount) - item_total) > 0.08:
        uncertain.append("reconciliation")
        confidence = min(confidence, 0.6)
    return {
        "line_number": line_number,
        "item_code": code,
        "description": description,
        "quantity": quantity,
        "unit": unit,
        "unit_price": unit_price,
        "gross_total": gross_total,
        "discount": discount,
        "item_total": item_total,
        "category": infer_category(description),
        "confidence": round(confidence, 3),
        "extraction_source": value.get("extraction_source", "llm"),
        "uncertain_fields": sorted(set(uncertain)),
        "ocr_text": str(value.get("evidence", "")),
    }


def validated_llm_items(result: dict, allowed_lines: set[int] | None = None) -> list[dict]:
    items = []
    seen = set()
    for raw in result.get("items", []):
        item = validated_llm_item(raw)
        if not item or item["line_number"] in seen:
            continue
        if allowed_lines is not None and item["line_number"] not in allowed_lines:
            continue
        item["extraction_source"] = result.get("provider", "llm")
        seen.add(item["line_number"])
        items.append(item)
    return sorted(items, key=lambda item: item["line_number"])
