from __future__ import annotations

from datetime import datetime


def analytics_rows(db, args) -> list[dict]:
    clauses = ["r.status = 'confirmed'"]
    params: list[str] = []
    if args.get("start"):
        clauses.append("date(r.purchased_at) >= date(?)")
        params.append(args["start"])
    if args.get("end"):
        clauses.append("date(r.purchased_at) <= date(?)")
        params.append(args["end"])
    if args.get("store"):
        clauses.append("r.merchant_cnpj = ?")
        params.append(args["store"])
    if args.get("category"):
        clauses.append("COALESCE(p.category, i.category_snapshot, 'Outros') = ?")
        params.append(args["category"])
    if args.get("q"):
        clauses.append("(i.description LIKE ? OR i.item_code LIKE ?)")
        params.extend([f"%{args['q']}%", f"%{args['q']}%"])
    sql = f"""
        SELECT
            i.id, i.receipt_id, i.item_code, i.description, i.quantity, i.unit,
            i.unit_price, i.gross_total, i.discount, i.item_total,
            COALESCE(p.category, i.category_snapshot, 'Outros') AS category,
            p.subcategory, p.brand, p.package_quantity, p.package_unit,
            p.units_per_package, r.purchased_at, r.merchant_name,
            r.merchant_cnpj, r.payment_method
        FROM receipt_items i
        JOIN receipts r ON r.id = i.receipt_id
        LEFT JOIN products p ON p.id = i.product_id
        WHERE {" AND ".join(clauses)}
        ORDER BY r.purchased_at, i.line_number, i.id
    """
    rows = []
    for source in db.execute(sql, params).fetchall():
        row = dict(source)
        quantity = float(row["quantity"] or 0)
        row["effective_price"] = float(row["item_total"] or 0) / quantity if quantity else 0
        dt = datetime.fromisoformat(row["purchased_at"])
        row["date"] = dt.date().isoformat()
        row["date_br"] = dt.strftime("%d/%m/%Y")
        row["hour"] = dt.strftime("%H:%M")
        row["day"] = dt.day
        row["period"] = "Início (1–10)" if dt.day <= 10 else "Meio (11–20)" if dt.day <= 20 else "Fim (21–31)"
        row["trip_key"] = str(row["receipt_id"])
        rows.append(row)
    return rows


def filter_options(db) -> dict:
    stores = [
        dict(row)
        for row in db.execute(
            """
            SELECT DISTINCT merchant_cnpj AS cnpj, merchant_name AS name
            FROM receipts
            WHERE status = 'confirmed'
            ORDER BY merchant_name, merchant_cnpj
            """
        ).fetchall()
    ]
    categories = [
        row[0]
        for row in db.execute(
            """
            SELECT DISTINCT COALESCE(p.category, i.category_snapshot, 'Outros')
            FROM receipt_items i
            JOIN receipts r ON r.id = i.receipt_id
            LEFT JOIN products p ON p.id = i.product_id
            WHERE r.status = 'confirmed'
            ORDER BY 1
            """
        ).fetchall()
    ]
    bounds = db.execute(
        """
        SELECT MIN(date(purchased_at)) AS start, MAX(date(purchased_at)) AS end
        FROM receipts WHERE status = 'confirmed'
        """
    ).fetchone()
    return {"stores": stores, "categories": categories, "start": bounds["start"], "end": bounds["end"]}

