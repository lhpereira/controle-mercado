import tempfile
import unittest
from pathlib import Path

from mercado import create_app


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


if __name__ == "__main__":
    unittest.main()
