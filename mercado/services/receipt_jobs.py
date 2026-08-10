from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..categories import infer_category


def enqueue_image_receipt(
    db: sqlite3.Connection,
    submission_id: str,
    image_path: str,
    ocr_mode: str,
) -> tuple[int, bool]:
    existing = db.execute(
        "SELECT id FROM receipts WHERE submission_id = ?", (submission_id,)
    ).fetchone()
    if existing:
        return int(existing["id"]), False
    try:
        cursor = db.execute(
            """
            INSERT INTO receipts (
                source_type, image_path, status, submission_id, ocr_mode
            ) VALUES ('imagem', ?, 'queued', ?, ?)
            """,
            (image_path, submission_id, ocr_mode),
        )
        db.commit()
        return int(cursor.lastrowid), True
    except sqlite3.IntegrityError:
        db.rollback()
        existing = db.execute(
            "SELECT id FROM receipts WHERE submission_id = ?", (submission_id,)
        ).fetchone()
        if not existing:
            raise
        return int(existing["id"]), False


def claim_next_receipt(db: sqlite3.Connection, stale_minutes: int = 20) -> dict | None:
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    db.execute(
        """
        UPDATE receipts
        SET status = 'queued', processing_started_at = NULL,
            processing_error = 'Processamento anterior interrompido; tarefa retomada.'
        WHERE status = 'processing'
          AND processing_started_at < datetime('now', ?)
        """,
        (f"-{max(1, stale_minutes)} minutes",),
    )
    row = db.execute(
        """
        SELECT id, image_path, ocr_mode
        FROM receipts
        WHERE status = 'queued' AND source_type = 'imagem'
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        db.commit()
        return None
    updated = db.execute(
        """
        UPDATE receipts
        SET status = 'processing', processing_started_at = CURRENT_TIMESTAMP,
            processing_error = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'queued'
        """,
        (row["id"],),
    )
    db.commit()
    return dict(row) if updated.rowcount == 1 else None


def finalize_receipt(db: sqlite3.Connection, receipt_id: int, parsed: dict) -> None:
    db.execute(
        """
        UPDATE receipts SET
            qr_url = ?, access_key = ?, receipt_number = ?, series = ?,
            merchant_name = ?, merchant_cnpj = ?, merchant_address = ?,
            purchased_at = ?, reported_item_count = ?, subtotal = ?,
            discount_total = ?, total_paid = ?, payment_method = ?, raw_text = ?,
            ocr_method = ?, ocr_warnings = ?, status = 'draft',
            processing_error = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            parsed.get("qr_url"),
            parsed.get("access_key"),
            parsed.get("receipt_number"),
            parsed.get("series"),
            parsed.get("merchant_name"),
            parsed.get("merchant_cnpj"),
            parsed.get("merchant_address"),
            parsed.get("purchased_at"),
            parsed.get("reported_item_count"),
            parsed.get("subtotal", 0),
            parsed.get("discount_total", 0),
            parsed.get("total_paid", 0),
            parsed.get("payment_method"),
            parsed.get("raw_text"),
            parsed.get("ocr_method"),
            json.dumps(parsed.get("ocr_warnings", []), ensure_ascii=False),
            receipt_id,
        ),
    )
    db.execute("DELETE FROM receipt_items WHERE receipt_id = ?", (receipt_id,))
    for item in parsed.get("items", []):
        db.execute(
            """
            INSERT INTO receipt_items (
                receipt_id, line_number, item_code, description, quantity, unit,
                unit_price, gross_total, discount, item_total, category_snapshot,
                confidence, extraction_source, uncertain_fields
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                item.get("line_number"),
                item.get("item_code"),
                item.get("description") or "Item sem descrição",
                item.get("quantity", 1),
                item.get("unit", "UN"),
                item.get("unit_price", 0),
                item.get("gross_total", 0),
                item.get("discount", 0),
                item.get("item_total", 0),
                item.get("category") or infer_category(item.get("description")),
                item.get("confidence"),
                item.get("extraction_source", "rapidocr"),
                json.dumps(item.get("uncertain_fields", []), ensure_ascii=False),
            ),
        )
    db.commit()


def fail_receipt(db: sqlite3.Connection, receipt_id: int, error: Exception | str) -> None:
    message = str(error).strip() or type(error).__name__
    db.execute(
        """
        UPDATE receipts
        SET status = 'failed', processing_error = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (message[:1000], receipt_id),
    )
    db.commit()


def retry_receipt(db: sqlite3.Connection, receipt_id: int) -> bool:
    updated = db.execute(
        """
        UPDATE receipts
        SET status = 'queued', processing_started_at = NULL,
            processing_error = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'failed'
        """,
        (receipt_id,),
    )
    db.commit()
    return updated.rowcount == 1


def remove_orphan_upload(upload_folder: str | Path, image_name: str) -> None:
    path = (Path(upload_folder) / image_name).resolve()
    root = Path(upload_folder).resolve()
    if path.parent == root and path.is_file():
        path.unlink()
