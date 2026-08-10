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
            merchant_name = after or None
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
        rf"(?:Valor\s+total|Total\s+a\s+pagar)\s*[:.]?\s*(?:R\$)?\s*({MONEY})",
        summary_text,
    )
    payment = first_match(
        r"Forma\s+de\s+pagamento\s+([^:\n]+)", summary_text
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
