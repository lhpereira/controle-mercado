import unittest

from mercado.services.fiscal_compare import compare_with_fiscal


def header(**overrides):
    base = {
        "merchant_cnpj": "00.111.222/0001-33",
        "subtotal": 10.00,
        "discount_total": 0,
        "total_paid": 10.00,
        "reported_item_count": 1,
        "items": [],
    }
    base.update(overrides)
    return base


def item(code="246", quantity=1, unit_price=10.0, item_total=10.0, description="ITEM TESTE"):
    return {
        "item_code": code,
        "description": description,
        "quantity": quantity,
        "unit_price": unit_price,
        "item_total": item_total,
    }


class FiscalCompareTest(unittest.TestCase):
    def test_no_differences_when_values_match(self):
        ocr = header(items=[item()])
        fiscal = header(items=[item()])
        self.assertEqual(compare_with_fiscal(ocr, fiscal), [])

    def test_money_differences_within_tolerance_are_ignored(self):
        ocr = header(total_paid=10.02)
        fiscal = header(total_paid=10.00)
        self.assertEqual(compare_with_fiscal(ocr, fiscal), [])

    def test_money_difference_beyond_tolerance_is_flagged(self):
        ocr = header(total_paid=10.50)
        fiscal = header(total_paid=10.00)
        differences = compare_with_fiscal(ocr, fiscal)
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0]["field"], "total_paid")

    def test_cnpj_is_compared_by_digits_only(self):
        ocr = header(merchant_cnpj="00.111.222/0001-33")
        fiscal = header(merchant_cnpj="00111222000133")
        self.assertEqual(compare_with_fiscal(ocr, fiscal), [])

    def test_cnpj_mismatch_is_flagged(self):
        ocr = header(merchant_cnpj="00.111.222/0001-33")
        fiscal = header(merchant_cnpj="99.888.777/0001-11")
        differences = compare_with_fiscal(ocr, fiscal)
        self.assertTrue(any(d["field"] == "merchant_cnpj" for d in differences))

    def test_missing_field_on_either_side_is_not_flagged(self):
        ocr = header(reported_item_count=None)
        fiscal = header(reported_item_count=3)
        self.assertEqual(compare_with_fiscal(ocr, fiscal), [])

    def test_item_present_only_in_fiscal_is_flagged(self):
        ocr = header(items=[])
        fiscal = header(items=[item(code="246")])
        differences = compare_with_fiscal(ocr, fiscal)
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0]["field"], "item_missing")

    def test_item_present_only_in_ocr_is_flagged(self):
        ocr = header(items=[item(code="246")])
        fiscal = header(items=[])
        differences = compare_with_fiscal(ocr, fiscal)
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0]["field"], "item_extra")

    def test_item_total_mismatch_is_flagged(self):
        ocr = header(items=[item(code="246", item_total=5.68)])
        fiscal = header(items=[item(code="246", item_total=6.98)])
        differences = compare_with_fiscal(ocr, fiscal)
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0]["field"], "item_total")

    def test_items_without_code_are_ignored_in_comparison(self):
        ocr = header(items=[item(code="")])
        fiscal = header(items=[item(code="")])
        self.assertEqual(compare_with_fiscal(ocr, fiscal), [])


if __name__ == "__main__":
    unittest.main()
