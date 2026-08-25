from __future__ import annotations

import sqlite3

from flask import current_app, g


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(
            current_app.config["DATABASE"],
            detect_types=sqlite3.PARSE_DECLTYPES,
            timeout=30,
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 30000")
    return g.db


def close_db(_error=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    # app e worker iniciam o mesmo banco. BEGIN IMMEDIATE serializa a
    # atualização para que apenas um processo execute cada ALTER TABLE.
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute("BEGIN IMMEDIATE")
        with current_app.open_resource("schema.sql") as schema:
            content = schema.read().decode("utf-8")
        for statement in content.split(";"):
            statement = statement.strip()
            if statement:
                db.execute(statement)
        migrations = {
            "receipts": {
                "user_id": "INTEGER",
                "ocr_method": "TEXT",
                "ocr_warnings": "TEXT",
                "submission_id": "TEXT",
                "ocr_mode": "TEXT",
                "processing_started_at": "TEXT",
                "processing_error": "TEXT",
                "fiscal_status": "TEXT",
                "fiscal_differences": "TEXT",
                "fiscal_checked_at": "TEXT",
            },
            "products": {
                "user_id": "INTEGER",
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
        db.execute("DROP INDEX IF EXISTS idx_products_scope_barcode")
        _scope_legacy_products(db)
        db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_products_user_scope_barcode
            ON products(user_id, merchant_cnpj, barcode)
            WHERE user_id IS NOT NULL AND barcode IS NOT NULL AND barcode <> ''
            """
        )
        db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_receipts_user_submission_id
            ON receipts(user_id, submission_id)
            WHERE user_id IS NOT NULL AND submission_id IS NOT NULL AND submission_id <> ''
            """
        )
        db.execute("DROP INDEX IF EXISTS idx_receipts_submission_id")
        db.execute("CREATE INDEX IF NOT EXISTS idx_receipts_user ON receipts(user_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_products_user ON products(user_id)")
        _ensure_owner_triggers(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def _scope_legacy_products(db: sqlite3.Connection) -> None:
    """Separa o catálogo compartilhado de instalações anteriores por dono."""
    rows = db.execute(
        """
        SELECT DISTINCT p.id AS product_id, r.user_id
        FROM products p
        JOIN receipt_items i ON i.product_id = p.id
        JOIN receipts r ON r.id = i.receipt_id
        WHERE p.user_id IS NULL AND r.user_id IS NOT NULL
        ORDER BY p.id, r.user_id
        """
    ).fetchall()
    owners: dict[int, list[int]] = {}
    for row in rows:
        owners.setdefault(row["product_id"], []).append(row["user_id"])
    for product_id, user_ids in owners.items():
        first_user_id, *other_user_ids = user_ids
        db.execute("UPDATE products SET user_id = ? WHERE id = ?", (first_user_id, product_id))
        for user_id in other_user_ids:
            cursor = db.execute(
                """
                INSERT INTO products (
                    user_id, barcode, merchant_cnpj, canonical_name, brand, category,
                    subcategory, package_quantity, package_unit, units_per_package, notes,
                    created_at, updated_at
                )
                SELECT ?, barcode, merchant_cnpj, canonical_name, brand, category,
                    subcategory, package_quantity, package_unit, units_per_package, notes,
                    created_at, updated_at
                FROM products WHERE id = ?
                """,
                (user_id, product_id),
            )
            db.execute(
                """
                UPDATE receipt_items SET product_id = ?
                WHERE product_id = ?
                  AND receipt_id IN (SELECT id FROM receipts WHERE user_id = ?)
                """,
                (cursor.lastrowid, product_id, user_id),
            )


def _ensure_owner_triggers(db: sqlite3.Connection) -> None:
    """Aplica a integridade de dono também nas tabelas legadas sem FK."""
    for table in ("receipts", "products"):
        for action in ("INSERT", "UPDATE"):
            db.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_{table}_user_{action.lower()}
                BEFORE {action} ON {table}
                WHEN NEW.user_id IS NOT NULL
                     AND NOT EXISTS (SELECT 1 FROM users WHERE id = NEW.user_id)
                BEGIN
                    SELECT RAISE(ABORT, 'invalid user_id');
                END
                """
            )


def init_app(app) -> None:
    app.teardown_appcontext(close_db)
