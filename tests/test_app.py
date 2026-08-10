import io
import tempfile
import unittest
import uuid
from html import unescape
from pathlib import Path
from unittest.mock import patch

from mercado import create_app
from mercado.db import get_db
from mercado.worker import process_one


class AppTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(root / "test.db"),
                "UPLOAD_FOLDER": str(root / "uploads"),
                "NFC_FETCH_ENABLED": False,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_health_and_pages(self):
        self.assertEqual(self.client.get("/api/health").json, {"status": "ok"})
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/receipts").status_code, 200)
        self.assertEqual(self.client.get("/products").status_code, 200)
        self.assertEqual(self.client.get("/receipts/new").status_code, 200)
        analytics = self.client.get("/api/analytics").json
        self.assertEqual(len(analytics["rows"]), 148)

    def test_manual_receipt_flow(self):
        response = self.client.post("/receipts/process")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/review", response.headers["Location"])
        receipt_id = int(response.headers["Location"].split("/")[-2])
        response = self.client.post(
            f"/receipts/{receipt_id}/save",
            data={
                "action": "confirm",
                "merchant_name": "Mercado Teste",
                "merchant_cnpj": "00.000.000/0001-00",
                "purchased_at": "2026-05-23T20:08",
                "reported_item_count": "1",
                "subtotal": "8,49",
                "discount_total": "0",
                "total_paid": "8,49",
                "payment_method": "Débito",
                "item_code[]": ["7892840800000"],
                "description[]": ["REFRIG PEPSI COLA 2L"],
                "quantity[]": ["1"],
                "unit[]": ["UN"],
                "unit_price[]": ["8,49"],
                "gross_total[]": ["8,49"],
                "discount[]": ["0"],
                "item_total[]": ["8,49"],
                "category[]": ["Bebidas sem álcool"],
            },
        )
        self.assertEqual(response.status_code, 302)
        analytics = self.client.get("/api/analytics").json
        self.assertEqual(len(analytics["rows"]), 149)

    def test_qr_url_creates_review_when_fetch_is_disabled(self):
        response = self.client.post(
            "/receipts/process",
            data={"receipt_url": "https://www.dfe.ms.gov.br/nfce/consulta"},
        )
        self.assertEqual(response.status_code, 302)
        review = self.client.get(response.headers["Location"])
        self.assertIn(b"Consulta autom", review.data)

    def test_database_has_llm_audit_and_scoped_product_columns(self):
        with self.app.app_context():
            receipt_columns = {
                row["name"] for row in get_db().execute("PRAGMA table_info(receipts)")
            }
            item_columns = {
                row["name"] for row in get_db().execute("PRAGMA table_info(receipt_items)")
            }
            product_columns = {
                row["name"] for row in get_db().execute("PRAGMA table_info(products)")
            }
        self.assertIn("ocr_method", receipt_columns)
        self.assertIn("ocr_warnings", receipt_columns)
        self.assertIn("extraction_source", item_columns)
        self.assertIn("uncertain_fields", item_columns)
        self.assertIn("merchant_cnpj", product_columns)

    @patch("mercado.worker.process_image")
    def test_image_flow_is_async_idempotent_and_shows_source(self, process_image):
        process_image.return_value = {
            "merchant_name": "Mercado Teste",
            "merchant_cnpj": "00.000.000/0001-00",
            "reported_item_count": 1,
            "subtotal": 5.68,
            "discount_total": 0,
            "total_paid": 5.68,
            "raw_text": "texto",
            "ocr_method": "hybrid:openai",
            "ocr_warnings": [],
            "items": [
                {
                    "line_number": 1,
                    "item_code": "246",
                    "description": "TOMATE SALADA KG",
                    "quantity": 0.825,
                    "unit": "KG",
                    "unit_price": 6.89,
                    "gross_total": 5.68,
                    "discount": 0,
                    "item_total": 5.68,
                    "category": "Hortifruti",
                    "confidence": 0.94,
                    "extraction_source": "openai",
                    "uncertain_fields": [],
                }
            ],
        }
        submission_id = str(uuid.uuid4())

        def submit():
            return self.client.post(
                "/receipts/process",
                data={
                    "submission_id": submission_id,
                    "ocr_mode": "hybrid",
                    "receipt_image": (io.BytesIO(b"fake-image"), "cupom.jpg"),
                },
                content_type="multipart/form-data",
            )

        first = submit()
        second = submit()
        self.assertEqual(first.status_code, 302)
        self.assertEqual(first.headers["Location"], second.headers["Location"])
        self.assertIn("/processing", first.headers["Location"])
        processing_page = self.client.get(first.headers["Location"])
        self.assertEqual(processing_page.status_code, 200)
        self.assertIn("continua em segundo plano".encode(), processing_page.data)
        rendered = unescape(processing_page.get_data(as_text=True))
        self.assertIn("Você pode sair desta página", rendered)
        self.assertIn("O cupom está na fila", rendered)
        self.assertEqual(processing_page.headers["Cache-Control"], "no-store")

        receipt_id = int(first.headers["Location"].split("/")[-2])
        status = self.client.get(f"/api/receipts/{receipt_id}/status").json
        self.assertEqual(status["status"], "queued")
        with self.app.app_context():
            db = get_db()
            count = db.execute(
                "SELECT COUNT(*) AS count FROM receipts WHERE submission_id = ?",
                (submission_id,),
            ).fetchone()["count"]
            db.execute(
                "UPDATE receipts SET status = 'processing' WHERE id = ?", (receipt_id,)
            )
            db.commit()
        self.assertEqual(count, 1)

        processing_page = self.client.get(first.headers["Location"])
        rendered = unescape(processing_page.get_data(as_text=True))
        self.assertIn("inteligência artificial estão", rendered)
        with self.app.app_context():
            db = get_db()
            db.execute("UPDATE receipts SET status = 'queued' WHERE id = ?", (receipt_id,))
            db.commit()

        self.assertTrue(process_one(self.app))
        status = self.client.get(f"/api/receipts/{receipt_id}/status").json
        self.assertEqual(status["status"], "draft")
        self.assertIn("/review", status["redirect_url"])
        review = self.client.get(status["redirect_url"])
        self.assertIn(b"hybrid:openai", review.data)
        self.assertIn(b"openai", review.data)

    @patch("mercado.worker.process_image", side_effect=RuntimeError("LLM indisponÃ­vel"))
    def test_failed_job_can_be_retried(self, _process_image):
        submission_id = str(uuid.uuid4())
        response = self.client.post(
            "/receipts/process",
            data={
                "submission_id": submission_id,
                "ocr_mode": "ollama",
                "receipt_image": (io.BytesIO(b"fake-image"), "cupom.jpg"),
            },
            content_type="multipart/form-data",
        )
        receipt_id = int(response.headers["Location"].split("/")[-2])
        self.assertTrue(process_one(self.app))
        status = self.client.get(f"/api/receipts/{receipt_id}/status").json
        self.assertEqual(status["status"], "failed")
        self.assertIn("LLM", status["error"])

        retry = self.client.post(f"/receipts/{receipt_id}/retry")
        self.assertEqual(retry.status_code, 302)
        status = self.client.get(f"/api/receipts/{receipt_id}/status").json
        self.assertEqual(status["status"], "queued")


if __name__ == "__main__":
    unittest.main()
