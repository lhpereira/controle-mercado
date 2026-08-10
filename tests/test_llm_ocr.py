import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from mercado.services.llm_ocr import (
    _image_data_url,
    extract_with_llm,
    validated_llm_item,
)
from mercado.services.ocr import process_image


def llm_receipt(items=None):
    return {
        "merchant_name": "Mercado Teste",
        "merchant_cnpj": "00.000.000/0001-00",
        "merchant_address": "Rua Teste",
        "purchased_at": "2026-08-09T10:00:00",
        "reported_item_count": len(items or []),
        "subtotal": 0,
        "discount_total": 0,
        "total_paid": 0,
        "payment_method": "",
        "receipt_number": "",
        "series": "",
        "access_key": "",
        "qr_url": "",
        "items": items or [],
    }


class LLMOCRTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.image = Path(self.temp.name) / "receipt.jpg"
        Image.new("RGB", (100, 200), "white").save(self.image)

    def tearDown(self):
        self.temp.cleanup()

    @patch("mercado.services.llm_ocr.requests.post")
    def test_openai_uses_responses_vision_and_json_schema(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": json.dumps(llm_receipt())}
                    ],
                }
            ]
        }
        post.return_value = response

        result = extract_with_llm(
            self.image,
            "openai",
            {
                "OPENAI_API_KEY": "test-key",
                "OPENAI_MODEL": "vision-test",
                "OPENAI_BASE_URL": "https://example.test/v1",
            },
        )

        self.assertEqual(result["provider"], "openai")
        call = post.call_args
        self.assertEqual(call.args[0], "https://example.test/v1/responses")
        payload = call.kwargs["json"]
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertTrue(
            payload["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,")
        )

    @patch("mercado.services.llm_ocr.requests.post")
    def test_ollama_uses_native_vision_endpoint_without_thinking(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "message": {"content": json.dumps(llm_receipt())}
        }
        post.return_value = response

        result = extract_with_llm(
            self.image,
            "ollama",
            {
                "OLLAMA_MODEL": "qwen3-vl:8b",
                "OLLAMA_BASE_URL": "http://ollama:11434/v1",
            },
            target_lines=[6, 7],
        )

        self.assertEqual(result["provider"], "ollama")
        call = post.call_args
        self.assertEqual(call.args[0], "http://ollama:11434/api/chat")
        payload = call.kwargs["json"]
        self.assertEqual(payload["format"]["type"], "object")
        self.assertFalse(payload["think"])
        self.assertFalse(payload["stream"])
        self.assertNotIn("data:image", payload["messages"][0]["images"][0])

    def test_accepts_short_store_plu_and_flags_bad_reconciliation(self):
        item = validated_llm_item(
            {
                "line_number": 11,
                "item_code": "246",
                "description": "TOMATE SALADA KG",
                "quantity": 0.825,
                "unit": "KG",
                "unit_price": 6.89,
                "gross_total": 5.68,
                "discount": 0,
                "item_total": 5.68,
                "confidence": 0.94,
                "uncertain_fields": [],
                "evidence": "246 TOMATE SALADA KG 0,825 KG 6,89 5,68",
            }
        )
        self.assertEqual(item["item_code"], "246")
        self.assertNotIn("reconciliation", item["uncertain_fields"])

    def test_hybrid_sends_a_compact_contact_sheet_for_target_lines(self):
        large_image = Path(self.temp.name) / "large-receipt.jpg"
        Image.new("RGB", (1200, 2000), "white").save(large_image)
        local_result = {
            "items": [
                {"line_number": 6, "_crop_box": [100, 500, 1100, 560]},
                {"line_number": 11, "_crop_box": [100, 900, 1100, 960]},
            ]
        }

        data_url = _image_data_url(large_image, local_result, [6, 11])
        encoded = data_url.split(",", 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as contact_sheet:
            self.assertLess(contact_sheet.height, 500)
            self.assertLess(contact_sheet.height, contact_sheet.width)

    @patch("mercado.services.ocr.decode_qr", return_value=None)
    @patch("mercado.services.ocr.extract_with_llm")
    @patch("mercado.services.ocr._local_process")
    def test_hybrid_replaces_only_doubtful_lines(self, local_process, llm_extract, _decode):
        local_process.return_value = {
            "merchant_name": "Mercado",
            "merchant_cnpj": "00.000.000/0001-00",
            "reported_item_count": 2,
            "subtotal": 10,
            "discount_total": 0,
            "total_paid": 10,
            "raw_text": "local",
            "ocr_method": "rapidocr",
            "ocr_warnings": [],
            "items": [
                {
                    "line_number": 1,
                    "item_code": "",
                    "description": "Item 1 — revisar OCR",
                    "quantity": 1,
                    "unit": "UN",
                    "unit_price": 0,
                    "gross_total": 0,
                    "discount": 0,
                    "item_total": 0,
                    "confidence": 0,
                },
                {
                    "line_number": 2,
                    "item_code": "7891234567890",
                    "description": "ITEM CERTO",
                    "quantity": 1,
                    "unit": "UN",
                    "unit_price": 5,
                    "gross_total": 5,
                    "discount": 0,
                    "item_total": 5,
                    "confidence": 0.95,
                },
            ],
        }
        corrected = llm_receipt(
            [
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
                    "confidence": 0.94,
                    "uncertain_fields": [],
                    "evidence": "246 TOMATE SALADA KG 0,825 KG 6,89 5,68",
                }
            ]
        )
        corrected.update({"provider": "openai", "model": "vision-test"})
        llm_extract.return_value = corrected

        result = process_image(
            self.image,
            mode="hybrid",
            llm_settings={"LLM_PROVIDER": "openai", "OCR_CONFIDENCE_THRESHOLD": 0.75},
        )

        self.assertEqual(result["items"][0]["item_code"], "246")
        self.assertEqual(result["items"][0]["extraction_source"], "openai")
        self.assertEqual(result["items"][1]["description"], "ITEM CERTO")
        self.assertEqual(llm_extract.call_args.kwargs["target_lines"], [1])


if __name__ == "__main__":
    unittest.main()
