from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

from ..categories import infer_category

MONEY = r"\d{1,6}(?:\.\d{3})*,\d{2}|\d{1,6}\.\d{2}"
UNITS = r"UN|KG|PT|CX|PC|PCT|FD|LT|L|G|ML"
ITEM_RE = re.compile(
    rf"^\s*(?:\d{{1,3}}\s+)?(?P<code>\d{{4,14}})\s+"
    rf"(?P<description>.+?)\s+"
    rf"(?P<quantity>\d+(?:[.,]\d{{1,3}})?)\s*"
    rf"(?P<unit>{UNITS})\s+"
    rf"(?P<unit_price>{MONEY})\s+"
    rf"(?P<total>{MONEY})\s*$",
    re.IGNORECASE,
)

STRUCTURED_MONEY_RE = re.compile(r"\d{1,6}[,.]\d{2}")
STRUCTURED_UNIT_RE = re.compile(
    rf"(?P<quantity>\d+(?:[.,]\d{{1,3}})?)\s*"
    rf"(?P<unit>{UNITS}|UH|UM|0N|ON|K6)\b",
    re.IGNORECASE,
)
NUMERIC_OCR_TRANSLATION = str.maketrans(
    {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8"}
)


def decimal_br(value: str | int | float | None) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    text = str(value).replace("R$", "").strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal("0")


def normalized(text: str) -> str:
    value = unicodedata.normalize("NFKD", text)
    return "".join(char for char in value if not unicodedata.combining(char))


def first_match(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    return match.group(1).strip()


def parse_datetime(text: str) -> str | None:
    match = re.search(
        r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}(?::\d{2})?)", text
    )
    if not match:
        noisy = re.search(
            r"\b(\d{2})7(\d{2})7(\d{4})\s+(\d{2}:\d{2}(?::\d{2})?)", text
        )
        if noisy:
            clock = noisy.group(4)
            match_value = f"{noisy.group(1)}/{noisy.group(2)}/{noisy.group(3)} {clock if clock.count(':') == 2 else clock + ':00'}"
            return datetime.strptime(match_value, "%d/%m/%Y %H:%M:%S").isoformat()
    if not match:
        date_only = re.search(r"(\d{2}/\d{2}/\d{4})", text)
        if not date_only:
            return None
        match_value = f"{date_only.group(1)} 12:00:00"
    else:
        clock = match.group(2)
        match_value = f"{match.group(1)} {clock if clock.count(':') == 2 else clock + ':00'}"
    return datetime.strptime(match_value, "%d/%m/%Y %H:%M:%S").isoformat()


def extract_fiscal_url(text: str) -> str | None:
    for line in text.splitlines():
        compact = re.sub(r"\s+", "", line)
        compact = re.sub(r"\.gnv\.br", ".gov.br", compact, flags=re.IGNORECASE)
        compact = re.sub(r"dfe\.ns\.gov\.br", "dfe.ms.gov.br", compact, flags=re.IGNORECASE)
        match = re.search(
            r"((?:https?://)?(?:www\.)?[a-z0-9.-]+\.gov\.br/[a-z0-9_/?=&.%+-]+)",
            compact,
            re.IGNORECASE,
        )
        if match:
            value = match.group(1).rstrip(".,;")
            return (
                value
                if value.lower().startswith(("http://", "https://"))
                else "https://" + value
            )
    return None


def parse_summary(lines: list[str], text: str) -> dict:
    plain = normalized(text)
    cnpj = first_match(r"CNPJ\s*[:.]?\s*([\d./-]{14,18})", plain)
    receipt_number = first_match(r"NFC[-eE ]*\s*([\d.]+)", plain)
    series = first_match(r"Serie\s*([\d]+)", plain)
    access_candidates = re.findall(r"(?:\d[\s.]*){44}", plain)
    access_key = None
    if access_candidates:
        access_key = re.sub(r"\D", "", access_candidates[-1])

    merchant_name = None
    merchant_address = None
    for index, line in enumerate(lines[:12]):
        if "CNPJ" in normalized(line).upper():
            after = re.sub(r".*?[\d./-]{14,18}\s*", "", line).strip(" :-")
            merchant_name = _clean_ocr_description(after) or None
            if index + 1 < len(lines):
                merchant_address = lines[index + 1].strip() or None
            break

    summary_text = plain
    marker = re.search(r"Qtd\.?\s*total\s*de\s*itens", plain, re.IGNORECASE)
    if marker:
        summary_text = plain[marker.start() :]

    item_count = first_match(
        r"Qtd\.?\s*total\s*de\s*itens\s*[:.]?\s*(\d+)", summary_text
    )
    subtotal = first_match(
        rf"(?:Total\s+produtos|Subtotal)\s*[:.]?\s*(?:R\$)?\s*({MONEY})",
        summary_text,
    )
    discount = first_match(
        rf"Desconto\s*[:.]?\s*(?:R\$)?\s*({MONEY})", summary_text
    )
    total = first_match(
        rf"(?:[VU]alor\s+total|Total\s+a\s+pagar)\s*[:.]?\s*(?:R[$s])?\s*({MONEY})",
        summary_text,
    )
    payment = first_match(
        r"For[mn]a\s+de\s+pagamento\s+([^:\n]+)", summary_text
    )

    return {
        "merchant_name": merchant_name,
        "merchant_cnpj": cnpj,
        "merchant_address": merchant_address,
        "purchased_at": parse_datetime(plain),
        "reported_item_count": int(item_count) if item_count else None,
        "subtotal": float(decimal_br(subtotal)),
        "discount_total": float(decimal_br(discount)),
        "total_paid": float(decimal_br(total)),
        "payment_method": payment,
        "receipt_number": receipt_number,
        "series": series,
        "access_key": access_key,
    }


def parse_items(lines: list[str]) -> list[dict]:
    items: list[dict] = []
    pending = ""
    for raw_line in lines:
        line = " ".join(raw_line.replace("|", " ").split())
        plain = normalized(line)
        if re.search(r"Qtd\.?\s*total\s*de\s*itens", plain, re.IGNORECASE):
            break
        discount_match = re.search(
            rf"desconto\s*[:.]?\s*(?:R\$)?\s*({MONEY})", plain, re.IGNORECASE
        )
        if discount_match and items:
            discount = decimal_br(discount_match.group(1))
            items[-1]["discount"] = float(discount)
            items[-1]["gross_total"] = float(
                decimal_br(items[-1]["item_total"]) + discount
            )
            continue

        candidate = f"{pending} {line}".strip() if pending else line
        match = ITEM_RE.match(candidate)
        if not match:
            if re.match(r"^\s*(?:\d{1,3}\s+)?\d{4,14}\s+", line):
                pending = line
            elif pending and len(line) < 100:
                pending = candidate
            continue

        quantity = decimal_br(match.group("quantity"))
        unit_price = decimal_br(match.group("unit_price"))
        item_total = decimal_br(match.group("total"))
        expected = quantity * unit_price
        discount = max(Decimal("0"), expected - item_total)
        description = match.group("description").strip(" -")
        items.append(
            {
                "line_number": len(items) + 1,
                "item_code": match.group("code"),
                "description": description,
                "quantity": float(quantity),
                "unit": match.group("unit").upper(),
                "unit_price": float(unit_price),
                "gross_total": float(expected),
                "discount": float(discount),
                "item_total": float(item_total),
                "category": infer_category(description),
                "confidence": 0.9,
            }
        )
        pending = ""
    return items


def _clean_ocr_description(value: str) -> str:
    """Corrige confusões seguras somente dentro de palavras do produto."""

    words = []
    for word in value.strip(" -").split():
        if word.startswith("0UE"):
            word = "QUE" + word[3:]
        if word and word[0].isalpha() and sum(char.isalpha() for char in word) >= 2:
            word = word.replace("0", "O").replace("1", "I")
        words.append(word)
    return " ".join(words)


def _ean13_is_valid(value: str) -> bool:
    if len(value) != 13 or not value.isdigit():
        return False
    expected = (10 - sum((1 if index % 2 == 0 else 3) * int(digit) for index, digit in enumerate(value[:12])) % 10) % 10
    return int(value[-1]) == expected


def parse_structured_item(text: str, line_number: int, ocr_confidence: float = 0.0) -> dict | None:
    """Lê uma linha já recortada do bloco fixo #/Cod/Descrição/Qt/Un/Vlr/Total.

    O número da linha vem da posição geométrica, pois é justamente um dos campos
    que mais sofre com confusões entre 0/D/O e 1/I no papel térmico.
    """

    line = " ".join(normalized(text).upper().replace("|", " ").split())
    money_matches = list(STRUCTURED_MONEY_RE.finditer(line))
    if len(money_matches) < 2:
        return None
    unit_price_match, total_match = money_matches[-2:]

    before_prices = line[: unit_price_match.start()]
    quantity_matches = list(STRUCTURED_UNIT_RE.finditer(before_prices))
    if not quantity_matches:
        return None
    quantity_match = quantity_matches[-1]

    prefix = before_prices[: quantity_match.start()]
    first_token = re.match(r"^\s*(?P<value>[\dODQILZSB]+)", prefix)
    if not first_token:
        return None
    first_digits = re.sub(r"\D", "", first_token.group("value").translate(NUMERIC_OCR_TRANSLATION))
    padded_number = f"{line_number:02d}"
    line_variants = (padded_number, str(line_number))
    if first_digits in line_variants:
        second_token = re.match(r"\s*(?P<value>[\dODQILZSB]{4,16})", prefix[first_token.end() :])
        if not second_token:
            return None
        digits = re.sub(r"\D", "", second_token.group("value").translate(NUMERIC_OCR_TRANSLATION))
        description_start = first_token.end() + second_token.end()
    else:
        digits = first_digits
        if digits.startswith(padded_number):
            digits = digits[len(padded_number) :]
        elif digits.startswith(str(line_number)):
            digits = digits[len(str(line_number)) :]
        description_start = first_token.end()
    if len(digits) > 13:
        valid_candidates = [part for part in (digits[:13], digits[-13:]) if _ean13_is_valid(part)]
        if valid_candidates:
            digits = valid_candidates[0]
    if not 3 <= len(digits) <= 14:
        return None

    description = _clean_ocr_description(prefix[description_start:])
    if not description:
        return None

    quantity = decimal_br(quantity_match.group("quantity"))
    unit_price = decimal_br(unit_price_match.group(0))
    item_total = decimal_br(total_match.group(0))
    unit = quantity_match.group("unit").upper()
    unit = {"UH": "UN", "UM": "UN", "0N": "UN", "ON": "UN", "K6": "KG"}.get(unit, unit)
    quantity_text = re.sub(r"\D", "", quantity_match.group("quantity"))
    if (
        unit == "UN"
        and item_total == unit_price
        and quantity != 1
        and quantity_text.endswith("1")
        and len(quantity_text) > 1
    ):
        description = _clean_ocr_description(f"{description} {quantity_text[:-1]}")
        quantity = Decimal("1")
    expected = quantity * unit_price
    discount = max(Decimal("0"), expected - item_total)
    difference = abs(expected - item_total)
    confidence = min(0.99, 0.65 + (max(0.0, min(1.0, ocr_confidence)) * 0.34))
    if difference > Decimal("0.06") and unit != "KG":
        confidence = min(confidence, 0.72)
    if any(word[0].isalpha() and any(char.isdigit() for char in word) for word in description.split()):
        confidence = max(0.0, confidence - 0.25)

    return {
        "line_number": line_number,
        "item_code": digits,
        "description": description,
        "quantity": float(quantity),
        "unit": unit,
        "unit_price": float(unit_price),
        "gross_total": float(expected),
        "discount": float(discount),
        "item_total": float(item_total),
        "category": infer_category(description),
        "confidence": round(confidence, 3),
        "ocr_text": text,
    }


def parse_receipt_text(text: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    receipt = parse_summary(lines, text)
    receipt["items"] = parse_items(lines)
    receipt["qr_url"] = extract_fiscal_url(text)
    if not receipt["subtotal"] and receipt["items"]:
        receipt["subtotal"] = sum(item["gross_total"] for item in receipt["items"])
    if not receipt["total_paid"] and receipt["items"]:
        receipt["total_paid"] = sum(item["item_total"] for item in receipt["items"])
    if not receipt["discount_total"] and receipt["items"]:
        receipt["discount_total"] = sum(item["discount"] for item in receipt["items"])
    return receipt
