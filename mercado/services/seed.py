from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..categories import infer_category
from .parser import decimal_br


def seed_legacy_data(db, seed_path: str | Path) -> int:
    if db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]:
        return 0
    path = Path(seed_path)
    if not path.exists():
        return 0
    values = json.loads(path.read_text(encoding="utf-8"))["values"]
    groups: dict[tuple[str, str], list[list[str]]] = {}
    for raw in values[1:]:
        row = raw + [""] * (12 - len(raw))
        groups.setdefault((row[7], row[8]), []).append(row)

    imported = 0
    for (date_br, cnpj), rows in sorted(groups.items()):
        purchased_at = datetime.strptime(date_br, "%d/%m/%Y").replace(hour=12).isoformat()
        subtotal = Decimal("0")
        discount_total = Decimal("0")
        total_paid = Decimal("0")
        for row in rows:
            quantity = decimal_br(row[3])
            unit_price = decimal_br(row[5])
            item_total = decimal_br(row[6])
            subtotal += quantity * unit_price
            discount_total += max(Decimal("0"), quantity * unit_price - item_total)
            total_paid += item_total
        cursor = db.execute(
            """
            INSERT INTO receipts (
                source_type, merchant_name, merchant_cnpj, purchased_at,
                reported_item_count, subtotal, discount_total, total_paid,
                status
            ) VALUES ('planilha', ?, ?, ?, ?, ?, ?, ?, 'confirmed')
            """,
            (
                rows[0][10] or rows[0][9],
                cnpj,
                purchased_at,
                len(rows),
                float(subtotal),
                float(discount_total),
                float(total_paid),
            ),
        )
        receipt_id = cursor.lastrowid
        for index, row in enumerate(rows, start=1):
            barcode = row[1].strip()
            description = row[2].strip()
            category = infer_category(description)
            product = (
                db.execute("SELECT id FROM products WHERE barcode = ?", (barcode,)).fetchone()
                if barcode
                else None
            )
            if product:
                product_id = product["id"]
            else:
                product_id = db.execute(
                    """
                    INSERT INTO products (barcode, canonical_name, category)
                    VALUES (?, ?, ?)
                    """,
                    (barcode or None, description, category),
                ).lastrowid
            quantity = decimal_br(row[3])
            unit_price = decimal_br(row[5])
            item_total = decimal_br(row[6])
            gross_total = quantity * unit_price
            db.execute(
                """
                INSERT INTO receipt_items (
                    receipt_id, product_id, line_number, item_code, description,
                    quantity, unit, unit_price, gross_total, discount, item_total,
                    category_snapshot, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    receipt_id,
                    product_id,
                    index,
                    barcode,
                    description,
                    float(quantity),
                    row[4],
                    float(unit_price),
                    float(gross_total),
                    float(max(Decimal("0"), gross_total - item_total)),
                    float(item_total),
                    category,
                ),
            )
        imported += 1
    db.commit()
    return imported

