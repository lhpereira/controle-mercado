from __future__ import annotations

from decimal import Decimal, InvalidOperation

MONEY_TOLERANCE = Decimal("0.05")
MONEY_FIELDS = {"subtotal", "discount_total", "total_paid"}

HEADER_FIELDS = [
    ("merchant_cnpj", "CNPJ do estabelecimento"),
    ("subtotal", "Subtotal"),
    ("discount_total", "Desconto total"),
    ("total_paid", "Total pago"),
    ("reported_item_count", "Quantidade de itens"),
]


def _digits(value) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return Decimal("0")


def _format(field: str, value) -> str:
    if value in (None, ""):
        return "—"
    if field in MONEY_FIELDS:
        return f"R$ {_decimal(value):.2f}".replace(".", ",")
    return str(value)


def _values_differ(field: str, ocr_value, fiscal_value) -> bool:
    if ocr_value in (None, "") or fiscal_value in (None, ""):
        return False
    if field == "merchant_cnpj":
        return _digits(ocr_value) != _digits(fiscal_value)
    if field in MONEY_FIELDS:
        return abs(_decimal(ocr_value) - _decimal(fiscal_value)) > MONEY_TOLERANCE
    if field == "reported_item_count":
        return int(ocr_value) != int(fiscal_value)
    return str(ocr_value).strip().lower() != str(fiscal_value).strip().lower()


def _item_key(item: dict) -> str | None:
    code = str(item.get("item_code") or "").strip()
    return code or None


def _indexed_items(parsed: dict) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for item in parsed.get("items", []):
        key = _item_key(item)
        if key:
            indexed[key] = item
    return indexed


def compare_with_fiscal(ocr: dict, fiscal: dict) -> list[dict]:
    """Compara os dados extraídos (OCR/revisão) com os dados oficiais da NFC-e."""

    differences = []
    for field, label in HEADER_FIELDS:
        ocr_value = ocr.get(field)
        fiscal_value = fiscal.get(field)
        if _values_differ(field, ocr_value, fiscal_value):
            differences.append(
                {
                    "field": field,
                    "label": label,
                    "ocr": _format(field, ocr_value),
                    "fiscal": _format(field, fiscal_value),
                }
            )

    ocr_items = _indexed_items(ocr)
    fiscal_items = _indexed_items(fiscal)

    for code, fiscal_item in fiscal_items.items():
        ocr_item = ocr_items.get(code)
        if ocr_item is None:
            differences.append(
                {
                    "field": "item_missing",
                    "label": f"Item {code} presente na NFC-e mas não no cupom lido",
                    "ocr": "—",
                    "fiscal": f"{fiscal_item.get('description', '')} · "
                    f"{_format('total_paid', fiscal_item.get('item_total'))}",
                }
            )
        elif _values_differ(
            "total_paid", ocr_item.get("item_total"), fiscal_item.get("item_total")
        ):
            differences.append(
                {
                    "field": "item_total",
                    "label": f"Item {code} — total divergente",
                    "ocr": _format("total_paid", ocr_item.get("item_total")),
                    "fiscal": _format("total_paid", fiscal_item.get("item_total")),
                }
            )

    for code, ocr_item in ocr_items.items():
        if code not in fiscal_items:
            differences.append(
                {
                    "field": "item_extra",
                    "label": f"Item {code} presente no cupom lido mas não na NFC-e",
                    "ocr": f"{ocr_item.get('description', '')} · "
                    f"{_format('total_paid', ocr_item.get('item_total'))}",
                    "fiscal": "—",
                }
            )

    return differences
