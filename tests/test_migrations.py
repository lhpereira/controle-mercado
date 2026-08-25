import sqlite3
import tempfile
import unittest
from pathlib import Path

from mercado import create_app


LEGACY_SCHEMA = """
CREATE TABLE receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL DEFAULT 'manual',
    image_path TEXT, qr_url TEXT, access_key TEXT, receipt_number TEXT, series TEXT,
    merchant_name TEXT, merchant_cnpj TEXT, merchant_address TEXT, purchased_at TEXT,
    reported_item_count INTEGER, subtotal NUMERIC NOT NULL DEFAULT 0,
    discount_total NUMERIC NOT NULL DEFAULT 0, total_paid NUMERIC NOT NULL DEFAULT 0,
    payment_method TEXT, raw_text TEXT, ocr_method TEXT, ocr_warnings TEXT,
    submission_id TEXT, ocr_mode TEXT, processing_started_at TEXT, processing_error TEXT,
    fiscal_status TEXT, fiscal_differences TEXT, fiscal_checked_at TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT, barcode TEXT, merchant_cnpj TEXT NOT NULL DEFAULT '',
    canonical_name TEXT NOT NULL, brand TEXT, category TEXT, subcategory TEXT,
    package_quantity NUMERIC, package_unit TEXT, units_per_package NUMERIC NOT NULL DEFAULT 1,
    notes TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE receipt_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    line_number INTEGER, item_code TEXT, description TEXT NOT NULL,
    quantity NUMERIC NOT NULL DEFAULT 1, unit TEXT NOT NULL DEFAULT 'UN',
    unit_price NUMERIC NOT NULL DEFAULT 0, gross_total NUMERIC NOT NULL DEFAULT 0,
    discount NUMERIC NOT NULL DEFAULT 0, item_total NUMERIC NOT NULL DEFAULT 0,
    category_snapshot TEXT, confidence NUMERIC,
    extraction_source TEXT NOT NULL DEFAULT 'manual', uncertain_fields TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_items_receipt ON receipt_items(receipt_id);
CREATE INDEX idx_items_product ON receipt_items(product_id);
CREATE INDEX idx_receipts_purchased_at ON receipts(purchased_at);
CREATE INDEX idx_receipts_cnpj ON receipts(merchant_cnpj);
"""


class LegacyMigrationTest(unittest.TestCase):
    """Simula um banco de uma instalação anterior à autenticação (sem `users`
    nem `receipts.user_id`) para garantir que o boot da aplicação migra o
    esquema automaticamente, sem crashar e sem perder dados existentes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "mercado.db"
        (self.root / "uploads").mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        conn.executescript(LEGACY_SCHEMA)
        conn.execute(
            """
            INSERT INTO receipts (merchant_name, merchant_cnpj, status, total_paid)
            VALUES ('Mercado Antigo', '00.000.000/0001-00', 'confirmed', 42.50)
            """
        )
        conn.execute(
            "INSERT INTO products (barcode, canonical_name, category) VALUES ('789123', 'Produto Antigo', 'Outros')"
        )
        conn.execute(
            "INSERT INTO receipt_items (receipt_id, product_id, description, item_total) "
            "VALUES (1, 1, 'Produto Antigo', 42.50)"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp.cleanup()

    def _create_app(self):
        return create_app(
            {
                "TESTING": True,
                "DATABASE": str(self.db_path),
                "UPLOAD_FOLDER": str(self.root / "uploads"),
                "NFC_FETCH_ENABLED": False,
                "SKIP_SEED": True,
            }
        )

    def test_boot_adds_missing_columns_without_losing_data(self):
        app = self._create_app()
        with app.app_context():
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(receipts)")}
            self.assertIn("user_id", columns)

            row = conn.execute(
                "SELECT merchant_name, total_paid FROM receipts WHERE id = 1"
            ).fetchone()
            self.assertEqual(row["merchant_name"], "Mercado Antigo")
            self.assertEqual(row["total_paid"], 42.50)

            item = conn.execute(
                "SELECT description FROM receipt_items WHERE receipt_id = 1"
            ).fetchone()
            self.assertEqual(item["description"], "Produto Antigo")

    def test_boot_is_idempotent_across_repeated_restarts(self):
        for _ in range(3):
            self._create_app()

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(receipts)")}
        self.assertIn("user_id", columns)
        row = conn.execute("SELECT merchant_name FROM receipts WHERE id = 1").fetchone()
        self.assertEqual(row["merchant_name"], "Mercado Antigo")

    def test_foreign_keys_still_enforced_after_migration(self):
        app = self._create_app()
        with app.app_context():
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA foreign_keys = ON")
            items_before = conn.execute(
                "SELECT COUNT(*) FROM receipt_items WHERE receipt_id = 1"
            ).fetchone()[0]
            self.assertEqual(items_before, 1)
            conn.execute("DELETE FROM receipts WHERE id = 1")
            conn.commit()
            items_after = conn.execute(
                "SELECT COUNT(*) FROM receipt_items WHERE receipt_id = 1"
            ).fetchone()[0]
            self.assertEqual(items_after, 0)


if __name__ == "__main__":
    unittest.main()
