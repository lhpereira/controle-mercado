from __future__ import annotations

import sqlite3

from flask import current_app, g


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    with current_app.open_resource("schema.sql") as schema:
        db.executescript(schema.read().decode("utf-8"))
    migrations = {
        "receipts": {
            "ocr_method": "TEXT",
            "ocr_warnings": "TEXT",
            "submission_id": "TEXT",
            "ocr_mode": "TEXT",
            "processing_started_at": "TEXT",
            "processing_error": "TEXT",
        },
        "products": {
            "merchant_cnpj": "TEXT NOT NULL DEFAULT ''",
        },
        "receipt_items": {
            "extraction_source": "TEXT NOT NULL DEFAULT 'manual'",
            "uncertain_fields": "TEXT NOT NULL DEFAULT '[]'",
        },
    }
    for table, columns in migrations.items():
        existing = {
            row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, declaration in columns.items():
            if column not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    db.execute("DROP INDEX IF EXISTS idx_products_barcode")
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_products_scope_barcode
        ON products(merchant_cnpj, barcode)
        WHERE barcode IS NOT NULL AND barcode <> ''
        """
    )
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_receipts_submission_id
        ON receipts(submission_id)
        WHERE submission_id IS NOT NULL AND submission_id <> ''
        """
    )
    db.commit()


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
