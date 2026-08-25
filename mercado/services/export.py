from __future__ import annotations

import sqlite3


def export_receipts_payload(db: sqlite3.Connection, user_id: int) -> dict:
    """Monta os dados de um usuário para backup/exportação.

    Inclui todos os cupons (qualquer status) com seus itens, e somente os
    produtos do catálogo compartilhado que foram realmente referenciados
    por esses itens — preserva nome/categoria sem expor o catálogo inteiro.
    """

    receipts = [
        dict(row)
        for row in db.execute(
            """
            SELECT * FROM receipts WHERE user_id = ?
            ORDER BY COALESCE(purchased_at, created_at), id
            """,
            (user_id,),
        ).fetchall()
    ]
    items_by_receipt: dict[int, list[dict]] = {receipt["id"]: [] for receipt in receipts}
    product_ids: set[int] = set()

    if receipts:
        placeholders = ",".join("?" * len(receipts))
        receipt_ids = [receipt["id"] for receipt in receipts]
        for row in db.execute(
            f"""
            SELECT * FROM receipt_items
            WHERE receipt_id IN ({placeholders})
            ORDER BY receipt_id, line_number, id
            """,
            receipt_ids,
        ).fetchall():
            item = dict(row)
            if item.get("product_id"):
                product_ids.add(item["product_id"])
            items_by_receipt[item["receipt_id"]].append(item)

    for receipt in receipts:
        receipt["items"] = items_by_receipt.get(receipt["id"], [])

    products = []
    if product_ids:
        placeholders = ",".join("?" * len(product_ids))
        products = [
            dict(row)
            for row in db.execute(
                f"SELECT * FROM products WHERE id IN ({placeholders})",
                list(product_ids),
            ).fetchall()
        ]

    return {"receipts": receipts, "products": products}
