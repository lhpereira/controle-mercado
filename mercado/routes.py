from __future__ import annotations

import json
import uuid
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

from .categories import CATEGORIES, infer_category
from .db import get_db
from .services.analytics import analytics_rows, filter_options
from .services.nfce import UnsafeReceiptURL, fetch_nfce
from .services.ocr import process_image
from .services.parser import decimal_br
from .services.seed import seed_legacy_data

bp = Blueprint("main", __name__)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "tif", "tiff"}


def get_receipt(receipt_id: int):
    receipt = get_db().execute("SELECT * FROM receipts WHERE id = ?", (receipt_id,)).fetchone()
    if receipt is None:
        abort(404)
    return receipt


def save_parsed_receipt(parsed: dict, source_type: str, image_path: str | None = None) -> int:
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO receipts (
            source_type, image_path, qr_url, access_key, receipt_number, series,
            merchant_name, merchant_cnpj, merchant_address, purchased_at,
            reported_item_count, subtotal, discount_total, total_paid,
            payment_method, raw_text, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
        """,
        (
            source_type,
            image_path,
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
        ),
    )
    receipt_id = cursor.lastrowid
    for item in parsed.get("items", []):
        db.execute(
            """
            INSERT INTO receipt_items (
                receipt_id, line_number, item_code, description, quantity, unit,
                unit_price, gross_total, discount, item_total, category_snapshot,
                confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
    db.commit()
    return receipt_id


@bp.route("/")
def dashboard():
    options = filter_options(get_db())
    return render_template("dashboard.html", options=options)


@bp.route("/receipts")
def receipts():
    rows = get_db().execute(
        """
        SELECT r.*, COUNT(i.id) AS item_rows
        FROM receipts r
        LEFT JOIN receipt_items i ON i.receipt_id = r.id
        GROUP BY r.id
        ORDER BY COALESCE(r.purchased_at, r.created_at) DESC
        """
    ).fetchall()
    return render_template("receipts.html", receipts=rows)


@bp.route("/receipts/new")
def new_receipt():
    return render_template("receipt_form.html")


@bp.post("/receipts/process")
def process_receipt():
    upload = request.files.get("receipt_image")
    receipt_url = request.form.get("receipt_url", "").strip()
    try:
        if upload and upload.filename:
            extension = Path(upload.filename).suffix.lower().lstrip(".")
            if extension not in ALLOWED_EXTENSIONS:
                raise ValueError("Formato não aceito. Use JPG, PNG, WEBP ou TIFF.")
            name = f"{uuid.uuid4().hex}.{extension}"
            destination = Path(current_app.config["UPLOAD_FOLDER"]) / secure_filename(name)
            upload.save(destination)
            parsed = process_image(destination, current_app.config["TESSERACT_LANG"])
            receipt_id = save_parsed_receipt(parsed, "imagem", name)
        elif receipt_url:
            if not current_app.config["NFC_FETCH_ENABLED"]:
                parsed = {
                    "qr_url": receipt_url,
                    "raw_text": "Consulta automática desativada. Revise os dados manualmente.",
                    "items": [],
                }
            else:
                parsed = fetch_nfce(receipt_url, current_app.config["NFC_ALLOWED_HOSTS"])
            receipt_id = save_parsed_receipt(parsed, "qr_url")
        else:
            receipt_id = save_parsed_receipt({"items": []}, "manual")
    except (ValueError, UnsafeReceiptURL, OSError) as error:
        flash(str(error), "error")
        return redirect(url_for("main.new_receipt"))
    return redirect(url_for("main.review_receipt", receipt_id=receipt_id))


@bp.route("/receipts/<int:receipt_id>/review")
def review_receipt(receipt_id: int):
    receipt = get_receipt(receipt_id)
    items = get_db().execute(
        "SELECT * FROM receipt_items WHERE receipt_id = ? ORDER BY line_number, id",
        (receipt_id,),
    ).fetchall()
    return render_template(
        "receipt_review.html", receipt=receipt, items=items, categories=CATEGORIES
    )


@bp.post("/receipts/<int:receipt_id>/save")
def save_receipt(receipt_id: int):
    get_receipt(receipt_id)
    db = get_db()
    status = "confirmed" if request.form.get("action") == "confirm" else "draft"
    db.execute(
        """
        UPDATE receipts SET
            merchant_name = ?, merchant_cnpj = ?, merchant_address = ?,
            purchased_at = ?, receipt_number = ?, series = ?, access_key = ?,
            reported_item_count = ?, subtotal = ?, discount_total = ?,
            total_paid = ?, payment_method = ?, qr_url = ?, status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            request.form.get("merchant_name"),
            request.form.get("merchant_cnpj"),
            request.form.get("merchant_address"),
            request.form.get("purchased_at") or None,
            request.form.get("receipt_number"),
            request.form.get("series"),
            request.form.get("access_key"),
            int(request.form.get("reported_item_count") or 0) or None,
            float(decimal_br(request.form.get("subtotal"))),
            float(decimal_br(request.form.get("discount_total"))),
            float(decimal_br(request.form.get("total_paid"))),
            request.form.get("payment_method"),
            request.form.get("qr_url"),
            status,
            receipt_id,
        ),
    )
    db.execute("DELETE FROM receipt_items WHERE receipt_id = ?", (receipt_id,))

    fields = {
        key: request.form.getlist(f"{key}[]")
        for key in [
            "item_code",
            "description",
            "quantity",
            "unit",
            "unit_price",
            "gross_total",
            "discount",
            "item_total",
            "category",
        ]
    }
    count = len(fields["description"])
    for index in range(count):
        description = fields["description"][index].strip()
        if not description:
            continue
        code = fields["item_code"][index].strip()
        category = fields["category"][index] or infer_category(description)
        product = (
            db.execute("SELECT * FROM products WHERE barcode = ?", (code,)).fetchone()
            if code
            else None
        )
        if product:
            product_id = product["id"]
            if not product["category"] or product["category"] == "Outros":
                db.execute(
                    "UPDATE products SET category = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (category, product_id),
                )
        else:
            product_id = db.execute(
                "INSERT INTO products (barcode, canonical_name, category) VALUES (?, ?, ?)",
                (code or None, description, category),
            ).lastrowid
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
                index + 1,
                code,
                description,
                float(decimal_br(fields["quantity"][index])),
                fields["unit"][index],
                float(decimal_br(fields["unit_price"][index])),
                float(decimal_br(fields["gross_total"][index])),
                float(decimal_br(fields["discount"][index])),
                float(decimal_br(fields["item_total"][index])),
                category,
            ),
        )
    db.commit()
    flash(
        "Cupom confirmado e incluído no painel."
        if status == "confirmed"
        else "Rascunho salvo.",
        "success",
    )
    return redirect(url_for("main.receipts"))


@bp.post("/receipts/<int:receipt_id>/delete")
def delete_receipt(receipt_id: int):
    receipt = get_receipt(receipt_id)
    if receipt["image_path"]:
        path = Path(current_app.config["UPLOAD_FOLDER"]) / receipt["image_path"]
        if path.exists():
            path.unlink()
    db = get_db()
    db.execute("DELETE FROM receipts WHERE id = ?", (receipt_id,))
    db.commit()
    flash("Cupom removido.", "success")
    return redirect(url_for("main.receipts"))


@bp.route("/receipts/<int:receipt_id>/image")
def receipt_image(receipt_id: int):
    receipt = get_receipt(receipt_id)
    if not receipt["image_path"]:
        abort(404)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], receipt["image_path"])


@bp.route("/products")
def products():
    rows = get_db().execute(
        """
        SELECT p.*, COUNT(i.id) AS purchases, COALESCE(SUM(i.item_total), 0) AS spend
        FROM products p
        LEFT JOIN receipt_items i ON i.product_id = p.id
        GROUP BY p.id
        ORDER BY p.canonical_name
        """
    ).fetchall()
    return render_template("products.html", products=rows, categories=CATEGORIES)


@bp.post("/products/<int:product_id>")
def update_product(product_id: int):
    db = get_db()
    if not db.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone():
        abort(404)
    db.execute(
        """
        UPDATE products SET canonical_name = ?, brand = ?, category = ?,
            subcategory = ?, package_quantity = ?, package_unit = ?,
            units_per_package = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            request.form.get("canonical_name"),
            request.form.get("brand"),
            request.form.get("category"),
            request.form.get("subcategory"),
            float(decimal_br(request.form.get("package_quantity"))) or None,
            request.form.get("package_unit"),
            float(decimal_br(request.form.get("units_per_package"))) or 1,
            request.form.get("notes"),
            product_id,
        ),
    )
    db.commit()
    flash("Produto atualizado.", "success")
    return redirect(url_for("main.products"))


@bp.route("/api/analytics")
def api_analytics():
    return jsonify({"rows": analytics_rows(get_db(), request.args)})


@bp.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@bp.post("/admin/seed")
def seed():
    seed_path = Path(current_app.root_path).parent / "data" / "seed_compras.json"
    imported = seed_legacy_data(get_db(), seed_path)
    flash(f"{imported} compras históricas importadas.", "success")
    return redirect(url_for("main.dashboard"))

