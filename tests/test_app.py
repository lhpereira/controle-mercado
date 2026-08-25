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
        self.client.post(
            "/register",
            data={
                "username": "tester",
                "password": "senha1234",
                "confirm_password": "senha1234",
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def _create_confirmed_receipt(self):
        response = self.client.post("/receipts/process")
        receipt_id = int(response.headers["Location"].split("/")[-2])
        self.client.post(
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
        return receipt_id

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
        self.assertIn("fiscal_status", receipt_columns)
        self.assertIn("user_id", receipt_columns)
        self.assertIn("fiscal_differences", receipt_columns)
        self.assertIn("fiscal_checked_at", receipt_columns)
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

    @patch("mercado.worker.process_image")
    def test_draft_receipt_can_be_reprocessed_with_another_engine(self, process_image):
        process_image.return_value = {
            "merchant_name": "Mercado Teste",
            "ocr_method": "rapidocr",
            "ocr_warnings": [],
            "items": [],
        }
        submission_id = str(uuid.uuid4())
        response = self.client.post(
            "/receipts/process",
            data={
                "submission_id": submission_id,
                "ocr_mode": "rapidocr",
                "receipt_image": (io.BytesIO(b"fake-image"), "cupom.jpg"),
            },
            content_type="multipart/form-data",
        )
        receipt_id = int(response.headers["Location"].split("/")[-2])
        self.assertTrue(process_one(self.app))
        status = self.client.get(f"/api/receipts/{receipt_id}/status").json
        self.assertEqual(status["status"], "draft")

        review = self.client.get(status["redirect_url"])
        self.assertIn(b"Reprocessar com outro motor", review.data)

        reprocess = self.client.post(
            f"/receipts/{receipt_id}/reprocess", data={"ocr_mode": "openai"}
        )
        self.assertEqual(reprocess.status_code, 302)
        self.assertIn("/processing", reprocess.headers["Location"])
        status = self.client.get(f"/api/receipts/{receipt_id}/status").json
        self.assertEqual(status["status"], "queued")
        with self.app.app_context():
            db = get_db()
            ocr_mode = db.execute(
                "SELECT ocr_mode FROM receipts WHERE id = ?", (receipt_id,)
            ).fetchone()["ocr_mode"]
        self.assertEqual(ocr_mode, "openai")

        invalid = self.client.post(
            f"/receipts/{receipt_id}/reprocess", data={"ocr_mode": "not-a-real-engine"}
        )
        self.assertEqual(invalid.status_code, 302)
        self.assertIn("/review", invalid.headers["Location"])

    @patch("mercado.worker.check_fiscal_data")
    @patch("mercado.worker.process_image")
    def test_image_receipt_is_compared_with_fiscal_data_when_enabled(
        self, process_image, check_fiscal_data
    ):
        self.app.config["NFC_FETCH_ENABLED"] = True
        process_image.return_value = {
            "merchant_name": "Mercado Teste",
            "qr_url": "https://www.dfe.ms.gov.br/nfce/consulta",
            "ocr_method": "rapidocr",
            "ocr_warnings": [],
            "items": [],
        }
        check_fiscal_data.return_value = {
            "fiscal_status": "diverging",
            "fiscal_differences": [
                {
                    "field": "subtotal",
                    "label": "Subtotal",
                    "ocr": "R$ 5,00",
                    "fiscal": "R$ 6,00",
                }
            ],
        }
        submission_id = str(uuid.uuid4())
        response = self.client.post(
            "/receipts/process",
            data={
                "submission_id": submission_id,
                "ocr_mode": "rapidocr",
                "receipt_image": (io.BytesIO(b"fake-image"), "cupom.jpg"),
            },
            content_type="multipart/form-data",
        )
        receipt_id = int(response.headers["Location"].split("/")[-2])
        self.assertTrue(process_one(self.app))
        check_fiscal_data.assert_called_once()

        with self.app.app_context():
            db = get_db()
            row = db.execute(
                "SELECT fiscal_status, fiscal_differences, fiscal_checked_at FROM receipts WHERE id = ?",
                (receipt_id,),
            ).fetchone()
        self.assertEqual(row["fiscal_status"], "diverging")
        self.assertIsNotNone(row["fiscal_checked_at"])
        self.assertIn("Subtotal", row["fiscal_differences"])

        review = self.client.get(f"/receipts/{receipt_id}/review")
        rendered = unescape(review.get_data(as_text=True))
        self.assertIn("divergência", rendered)
        self.assertIn("Subtotal", rendered)

    @patch("mercado.routes.check_fiscal_data")
    def test_manual_fiscal_verification_updates_status(self, check_fiscal_data):
        self.app.config["NFC_FETCH_ENABLED"] = True
        check_fiscal_data.return_value = {
            "fiscal_status": "matched",
            "fiscal_differences": [],
        }
        response = self.client.post("/receipts/process")
        receipt_id = int(response.headers["Location"].split("/")[-2])
        self.client.post(
            f"/receipts/{receipt_id}/save",
            data={
                "action": "draft",
                "merchant_name": "Mercado Teste",
                "qr_url": "https://www.dfe.ms.gov.br/nfce/consulta",
                "subtotal": "10,00",
                "discount_total": "0",
                "total_paid": "10,00",
            },
        )

        verify = self.client.post(f"/receipts/{receipt_id}/verify-fiscal")
        self.assertEqual(verify.status_code, 302)
        check_fiscal_data.assert_called_once()
        with self.app.app_context():
            db = get_db()
            status = db.execute(
                "SELECT fiscal_status FROM receipts WHERE id = ?", (receipt_id,)
            ).fetchone()["fiscal_status"]
        self.assertEqual(status, "matched")

    def test_fiscal_verification_requires_qr_url_and_enabled_flag(self):
        response = self.client.post("/receipts/process")
        receipt_id = int(response.headers["Location"].split("/")[-2])

        no_qr = self.client.post(f"/receipts/{receipt_id}/verify-fiscal")
        self.assertEqual(no_qr.status_code, 302)
        with self.app.app_context():
            db = get_db()
            status = db.execute(
                "SELECT fiscal_status FROM receipts WHERE id = ?", (receipt_id,)
            ).fetchone()["fiscal_status"]
        self.assertIsNone(status)

    def test_registration_requires_matching_passwords(self):
        self.client.post("/logout")
        response = self.client.post(
            "/register",
            data={
                "username": "outro",
                "password": "senha1234",
                "confirm_password": "diferente",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("não coincidem".encode(), response.data)

    def test_registration_rejects_short_password(self):
        self.client.post("/logout")
        response = self.client.post(
            "/register",
            data={"username": "outro", "password": "curta", "confirm_password": "curta"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("8 caracteres".encode(), response.data)

    def test_registration_rejects_duplicate_username(self):
        self.client.post("/logout")
        response = self.client.post(
            "/register",
            data={
                "username": "tester",
                "password": "outrasenha1",
                "confirm_password": "outrasenha1",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("já está em uso".encode(), response.data)

    def test_login_rejects_wrong_password(self):
        self.client.post("/logout")
        response = self.client.post(
            "/login", data={"username": "tester", "password": "senhaerrada"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("inválidos".encode(), response.data)

    def test_logged_out_user_is_redirected_to_login(self):
        self.client.post("/logout")
        response = self.client.get("/receipts")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_health_endpoint_stays_public(self):
        self.client.post("/logout")
        self.assertEqual(self.client.get("/api/health").json, {"status": "ok"})

    def test_users_cannot_see_each_others_receipts(self):
        response = self.client.post("/receipts/process")
        receipt_id = int(response.headers["Location"].split("/")[-2])
        self.assertEqual(
            self.client.get(f"/receipts/{receipt_id}/review").status_code, 200
        )

        self.client.post("/logout")
        self.client.post(
            "/register",
            data={
                "username": "outra_pessoa",
                "password": "outrasenha1",
                "confirm_password": "outrasenha1",
            },
        )
        other_review = self.client.get(f"/receipts/{receipt_id}/review")
        self.assertEqual(other_review.status_code, 404)

        with self.app.app_context():
            db = get_db()
            count_for_second_user = db.execute(
                """
                SELECT COUNT(*) FROM receipts
                WHERE user_id = (SELECT id FROM users WHERE username = 'outra_pessoa')
                """
            ).fetchone()[0]
        self.assertEqual(count_for_second_user, 0)

    def test_export_json_contains_only_current_user_receipts(self):
        receipt_id = self._create_confirmed_receipt()

        response = self.client.get("/export/receipts.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        data = response.json
        self.assertEqual(data["username"], "tester")
        exported = next(r for r in data["receipts"] if r["id"] == receipt_id)
        self.assertEqual(len(exported["items"]), 1)
        self.assertEqual(exported["items"][0]["description"], "REFRIG PEPSI COLA 2L")
        self.assertTrue(any(p["barcode"] == "7892840800000" for p in data["products"]))

        self.client.post("/logout")
        self.client.post(
            "/register",
            data={
                "username": "outra_pessoa",
                "password": "outrasenha1",
                "confirm_password": "outrasenha1",
            },
        )
        other = self.client.get("/export/receipts.json").json
        self.assertEqual(other["receipts"], [])
        self.assertEqual(other["products"], [])

    def test_export_csv_contains_item_rows(self):
        self._create_confirmed_receipt()

        response = self.client.get("/export/receipts.csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        body = response.get_data(as_text=True)
        self.assertIn("cupom_id,status,estabelecimento", body)
        self.assertIn("REFRIG PEPSI COLA 2L", body)

    def test_export_requires_login(self):
        self.client.post("/logout")
        response = self.client.get("/export/receipts.json")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
