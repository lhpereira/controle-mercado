PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL DEFAULT 'manual',
    image_path TEXT,
    qr_url TEXT,
    access_key TEXT,
    receipt_number TEXT,
    series TEXT,
    merchant_name TEXT,
    merchant_cnpj TEXT,
    merchant_address TEXT,
    purchased_at TEXT,
    reported_item_count INTEGER,
    subtotal NUMERIC NOT NULL DEFAULT 0,
    discount_total NUMERIC NOT NULL DEFAULT 0,
    total_paid NUMERIC NOT NULL DEFAULT 0,
    payment_method TEXT,
    raw_text TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    barcode TEXT,
    canonical_name TEXT NOT NULL,
    brand TEXT,
    category TEXT,
    subcategory TEXT,
    package_quantity NUMERIC,
    package_unit TEXT,
    units_per_package NUMERIC NOT NULL DEFAULT 1,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode
ON products(barcode) WHERE barcode IS NOT NULL AND barcode <> '';

CREATE TABLE IF NOT EXISTS receipt_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    line_number INTEGER,
    item_code TEXT,
    description TEXT NOT NULL,
    quantity NUMERIC NOT NULL DEFAULT 1,
    unit TEXT NOT NULL DEFAULT 'UN',
    unit_price NUMERIC NOT NULL DEFAULT 0,
    gross_total NUMERIC NOT NULL DEFAULT 0,
    discount NUMERIC NOT NULL DEFAULT 0,
    item_total NUMERIC NOT NULL DEFAULT 0,
    category_snapshot TEXT,
    confidence NUMERIC,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_items_receipt ON receipt_items(receipt_id);
CREATE INDEX IF NOT EXISTS idx_items_product ON receipt_items(product_id);
CREATE INDEX IF NOT EXISTS idx_receipts_purchased_at ON receipts(purchased_at);
CREATE INDEX IF NOT EXISTS idx_receipts_cnpj ON receipts(merchant_cnpj);

