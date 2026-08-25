from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.utils import secure_filename

from .auth import login_required
from .categories import CATEGORIES, infer_category
from .db import get_db
from .services.analytics import analytics_rows, filter_options
from .services.export import export_receipts_payload
from .services.nfce import UnsafeReceiptURL, check_fiscal_data, fetch_nfce
from .services.parser import decimal_br
from .services.receipt_jobs import (
    enqueue_image_receipt,
    remove_orphan_upload,
    reprocess_receipt,
    retry_receipt,
)
from .services.seed import seed_legacy_data

bp = Blueprint("main", __name__)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "tif", "tiff"}


def get_receipt(receipt_id: int):
    receipt = get_db().execute(
        "SELECT * FROM receipts WHERE id = ? AND user_id = ?", (receipt_id, g.user["id"])
    ).fetchone()
    if receipt is None:
        abort(404)
    return receipt


def save_parsed_receipt(parsed: dict, source_type: str, image_path: str | None = None) -> int:
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO receipts (
            user_id, source_type, image_path, qr_url, access_key, receipt_number, series,
            merchant_name, merchant_cnpj, merchant_address, purchased_at,
            reported_item_count, subtotal, discount_total, total_paid,
            payment_method, raw_text, ocr_method, ocr_warnings, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
        """,
        (
            g.user["id"],
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
            parsed.get("ocr_method"),
            json.dumps(parsed.get("ocr_warnings", []), ensure_ascii=False),
        ),
    )
    receipt_id = cursor.lastrowid
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
    return receipt_id


def enrich_items_from_catalog(parsed: dict) -> None:
    """Reaproveita a descrição revisada quando o Cod já existe no catálogo."""

    db = get_db()
    for item in parsed.get("items", []):
        code = str(item.get("item_code") or "").strip()
        if not code:
            continue
        scope = product_scope(code, parsed.get("merchant_cnpj"))
        product = db.execute(
            "SELECT canonical_name, category FROM products WHERE merchant_cnpj = ? AND barcode = ?",
            (scope, code),
        ).fetchone()
        if product:
            item["description"] = product["canonical_name"]
            item["category"] = product["category"] or item.get("category")
            item["confidence"] = 1.0
            item["extraction_source"] = "catalog"
            item["uncertain_fields"] = []


def product_scope(code: str, merchant_cnpj: str | None) -> str:
    """EANs são globais; PLUs curtos ficam no escopo do estabelecimento."""

    return "" if len(code) >= 8 else (merchant_cnpj or "")


@bp.route("/")
@login_required
def dashboard():
    options = filter_options(get_db(), g.user["id"])
    return render_template("dashboard.html", options=options)


@bp.route("/receipts")
@login_required
def receipts():
    rows = get_db().execute(
        """
        SELECT r.*, COUNT(i.id) AS item_rows
        FROM receipts r
        LEFT JOIN receipt_items i ON i.receipt_id = r.id
        WHERE r.user_id = ?
        GROUP BY r.id
        ORDER BY COALESCE(r.purchased_at, r.created_at) DESC
        """,
        (g.user["id"],),
    ).fetchall()
    return render_template("receipts.html", receipts=rows)


@bp.route("/export/receipts.json")
@login_required
def export_receipts_json():
    payload = export_receipts_payload(get_db(), g.user["id"])
    stamp = datetime.now(timezone.utc)
    response = jsonify(
        {
            "exported_at": stamp.isoformat(),
            "username": g.user["username"],
            "receipts": payload["receipts"],
            "products": payload["products"],
        }
    )
    filename = f"controle-mercado-{g.user['username']}-{stamp:%Y%m%d}.json"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@bp.route("/export/receipts.csv")
@login_required
def export_receipts_csv():
    payload = export_receipts_payload(get_db(), g.user["id"])
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "cupom_id", "status", "estabelecimento", "cnpj", "data_hora",
            "linha", "codigo", "descricao", "quantidade", "unidade",
            "preco_unitario", "valor_bruto", "desconto", "valor_total", "categoria",
        ]
    )
    for receipt in payload["receipts"]:
        for item in receipt["items"]:
            writer.writerow(
                [
                    receipt["id"],
                    receipt["status"],
                    receipt.get("merchant_name") or "",
                    receipt.get("merchant_cnpj") or "",
                    receipt.get("purchased_at") or "",
                    item.get("line_number"),
                    item.get("item_code") or "",
                    item.get("description"),
                    item.get("quantity"),
                    item.get("unit"),
                    item.get("unit_price"),
                    item.get("gross_total"),
                    item.get("discount"),
                    item.get("item_total"),
                    item.get("category_snapshot") or "",
                ]
            )
    stamp = datetime.now(timezone.utc)
    response = make_response(buffer.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    filename = f"controle-mercado-{g.user['username']}-{stamp:%Y%m%d}.csv"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@bp.route("/receipts/new")
@login_required
def new_receipt():
    return render_template(
        "receipt_form.html",
        default_ocr_provider=current_app.config["OCR_PROVIDER"],
        llm_provider=current_app.config["LLM_PROVIDER"],
        submission_id=str(uuid.uuid4()),
    )


@bp.post("/receipts/process")
@login_required
def process_receipt():
    upload = request.files.get("receipt_image")
    receipt_url = request.form.get("receipt_url", "").strip()
    try:
        if upload and upload.filename:
            try:
                submission_id = str(uuid.UUID(request.form.get("submission_id", "")))
            except ValueError:
                submission_id = str(uuid.uuid4())
            existing = get_db().execute(
                "SELECT id FROM receipts WHERE submission_id = ? AND user_id = ?",
                (submission_id, g.user["id"]),
            ).fetchone()
            if existing:
                return redirect(
                    url_for("main.processing_receipt", receipt_id=existing["id"])
                )
            extension = Path(upload.filename).suffix.lower().lstrip(".")
            if extension not in ALLOWED_EXTENSIONS:
                raise ValueError("Formato não aceito. Use JPG, PNG, WEBP ou TIFF.")
            name = f"{uuid.uuid4().hex}.{extension}"
            destination = Path(current_app.config["UPLOAD_FOLDER"]) / secure_filename(name)
            upload.save(destination)
            ocr_mode = request.form.get("ocr_mode") or current_app.config["OCR_PROVIDER"]
            receipt_id, created = enqueue_image_receipt(
                get_db(), g.user["id"], submission_id, name, ocr_mode
            )
            if not created:
                remove_orphan_upload(current_app.config["UPLOAD_FOLDER"], name)
            return redirect(url_for("main.processing_receipt", receipt_id=receipt_id))
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
@login_required
def review_receipt(receipt_id: int):
    receipt = get_receipt(receipt_id)
    if receipt["status"] in {"queued", "processing", "failed"}:
        return redirect(url_for("main.processing_receipt", receipt_id=receipt_id))
    items = get_db().execute(
        "SELECT * FROM receipt_items WHERE receipt_id = ? ORDER BY line_number, id",
        (receipt_id,),
    ).fetchall()
    ocr_stats = {
        "rows": len(items),
        "expected": receipt["reported_item_count"],
        "low_confidence": sum(
            1 for item in items if item["confidence"] is None or item["confidence"] < 0.75
        ),
    }
    try:
        ocr_warnings = json.loads(receipt["ocr_warnings"] or "[]")
    except (TypeError, json.JSONDecodeError):
        ocr_warnings = [receipt["ocr_warnings"]] if receipt["ocr_warnings"] else []
    try:
        fiscal_differences = json.loads(receipt["fiscal_differences"] or "null")
    except (TypeError, json.JSONDecodeError):
        fiscal_differences = None
    return render_template(
        "receipt_review.html",
        receipt=receipt,
        items=items,
        categories=CATEGORIES,
        ocr_stats=ocr_stats,
        ocr_warnings=ocr_warnings,
        fiscal_differences=fiscal_differences,
        nfc_fetch_enabled=current_app.config["NFC_FETCH_ENABLED"],
        llm_provider=current_app.config["LLM_PROVIDER"],
    )


@bp.route("/receipts/<int:receipt_id>/processing")
@login_required
def processing_receipt(receipt_id: int):
    receipt = get_receipt(receipt_id)
    if receipt["status"] in {"draft", "confirmed"}:
        return redirect(url_for("main.review_receipt", receipt_id=receipt_id))
    response = make_response(render_template("receipt_processing.html", receipt=receipt))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/api/receipts/<int:receipt_id>/status")
@login_required
def receipt_processing_status(receipt_id: int):
    receipt = get_receipt(receipt_id)
    result = {
        "status": receipt["status"],
        "error": receipt["processing_error"],
    }
    if receipt["status"] in {"draft", "confirmed"}:
        result["redirect_url"] = url_for("main.review_receipt", receipt_id=receipt_id)
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/receipts/<int:receipt_id>/retry")
@login_required
def retry_processing_receipt(receipt_id: int):
    get_receipt(receipt_id)
    if retry_receipt(get_db(), receipt_id):
        flash("Processamento colocado novamente na fila.", "success")
    return redirect(url_for("main.processing_receipt", receipt_id=receipt_id))


@bp.post("/receipts/<int:receipt_id>/reprocess")
@login_required
def reprocess_receipt_route(receipt_id: int):
    get_receipt(receipt_id)
    ocr_mode = request.form.get("ocr_mode") or current_app.config["OCR_PROVIDER"]
    if ocr_mode not in {"hybrid", "rapidocr", "openai", "ollama"}:
        flash("Motor de leitura inválido.", "error")
        return redirect(url_for("main.review_receipt", receipt_id=receipt_id))
    if reprocess_receipt(get_db(), receipt_id, ocr_mode):
        flash("Cupom enviado para reprocessamento com outro motor.", "success")
        return redirect(url_for("main.processing_receipt", receipt_id=receipt_id))
    flash("Não foi possível reprocessar este cupom.", "error")
    return redirect(url_for("main.review_receipt", receipt_id=receipt_id))


@bp.post("/receipts/<int:receipt_id>/verify-fiscal")
@login_required
def verify_fiscal_data(receipt_id: int):
    receipt = get_receipt(receipt_id)
    if not receipt["qr_url"]:
        flash("Este cupom não possui QR Code de NFC-e para comparar.", "error")
        return redirect(url_for("main.review_receipt", receipt_id=receipt_id))
    if not current_app.config["NFC_FETCH_ENABLED"]:
        flash("A consulta automática ao portal fiscal está desativada.", "error")
        return redirect(url_for("main.review_receipt", receipt_id=receipt_id))

    db = get_db()
    items = db.execute(
        "SELECT * FROM receipt_items WHERE receipt_id = ? ORDER BY line_number, id",
        (receipt_id,),
    ).fetchall()
    snapshot = {
        "qr_url": receipt["qr_url"],
        "merchant_cnpj": receipt["merchant_cnpj"],
        "subtotal": receipt["subtotal"],
        "discount_total": receipt["discount_total"],
        "total_paid": receipt["total_paid"],
        "reported_item_count": receipt["reported_item_count"],
        "items": [dict(item) for item in items],
    }
    result = check_fiscal_data(snapshot, current_app.config["NFC_ALLOWED_HOSTS"])
    differences = result.get("fiscal_differences")
    db.execute(
        """
        UPDATE receipts
        SET fiscal_status = ?, fiscal_differences = ?, fiscal_checked_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            result["fiscal_status"],
            json.dumps(differences, ensure_ascii=False) if differences is not None else None,
            receipt_id,
        ),
    )
    db.commit()
    if result.get("fiscal_warning"):
        flash(result["fiscal_warning"], "error")
    elif result["fiscal_status"] == "matched":
        flash("Os dados conferem com a NFC-e.", "success")
    else:
        flash(f"{len(differences)} divergência(s) encontradas com a NFC-e.", "error")
    return redirect(url_for("main.review_receipt", receipt_id=receipt_id))


@bp.post("/receipts/<int:receipt_id>/save")
@login_required
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
        scope = product_scope(code, request.form.get("merchant_cnpj"))
        product = (
            db.execute(
                "SELECT * FROM products WHERE merchant_cnpj = ? AND barcode = ?",
                (scope, code),
            ).fetchone()
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
                "INSERT INTO products (barcode, merchant_cnpj, canonical_name, category) VALUES (?, ?, ?, ?)",
                (code or None, scope, description, category),
            ).lastrowid
        db.execute(
            """
            INSERT INTO receipt_items (
                receipt_id, product_id, line_number, item_code, description,
                quantity, unit, unit_price, gross_total, discount, item_total,
                category_snapshot, confidence
                , extraction_source, uncertain_fields
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'manual_review', '[]')
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
@login_required
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
@login_required
def receipt_image(receipt_id: int):
    receipt = get_receipt(receipt_id)
    if not receipt["image_path"]:
        abort(404)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], receipt["image_path"])


@bp.route("/products")
@login_required
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
@login_required
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
@login_required
def api_analytics():
    return jsonify({"rows": analytics_rows(get_db(), request.args, g.user["id"])})


@bp.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@bp.post("/admin/seed")
@login_required
def seed():
    seed_path = Path(current_app.root_path).parent / "data" / "seed_compras.json"
    imported = seed_legacy_data(get_db(), seed_path)
    flash(f"{imported} compras históricas importadas.", "success")
    return redirect(url_for("main.dashboard"))
