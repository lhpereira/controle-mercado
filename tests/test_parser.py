import unittest

from mercado.services.parser import parse_receipt_text, parse_structured_item


SAMPLE = """
COMPER
CNPJ: 09.477.652/0111-20 SDB COMERCIO DE ALIMENTOS LTDA
Avenida Mascarenhas de Moraes, 60
7892840800000 REFRIG PEPSI COLA 2L 1 UN 8,49 8,49
7804300010638 VN CHI GATO N 750ML CH 1 UN 59,90 59,90
7898341430098 SUCO PB NECT DEL VALLE M 1 UN 9,69 9,69
10396 QUEIJO BRIE VIGOR KG 0,130 KG 109,98 14,00
Desconto: 0,30
1734 QJ MINAS FRESCAL IMBAUBA 0,365 KG 69,90 20,00
Desconto: 5,51
7895600304211 CHICLE TRIDENT 8G 1 UN 3,69 3,69
7896045505371 CERV HEINEKEN 330M 1,000 PT 33,48 33,48
Qtd. total de itens 7
Total produtos R$ 155,06
Desconto R$ 5,81
Valor total R$ 149,25
Forma de pagamento Cartao de Debito: 149,25
NFCe 123.302 Serie 111
23/05/2026 20:08:23
www.dfe.ms.gov.br/nfce/consulta
"""


class ReceiptParserTest(unittest.TestCase):
    def test_parses_receipt_and_item_discounts(self):
        result = parse_receipt_text(SAMPLE)
        self.assertEqual(result["merchant_cnpj"], "09.477.652/0111-20")
        self.assertEqual(result["reported_item_count"], 7)
        self.assertEqual(result["payment_method"], "Cartao de Debito")
        self.assertAlmostEqual(result["total_paid"], 149.25)
        self.assertEqual(len(result["items"]), 7)
        self.assertAlmostEqual(result["items"][3]["discount"], 0.30)
        self.assertAlmostEqual(result["items"][4]["discount"], 5.51)
        self.assertEqual(result["purchased_at"], "2026-05-23T20:08:23")
        self.assertEqual(
            result["qr_url"], "https://www.dfe.ms.gov.br/nfce/consulta"
        )

    def test_parses_fixed_receipt_columns_even_when_spacing_is_lost(self):
        item = parse_structured_item(
            "017898951850217 MASSA LASANHA BARILLA 21 UN 7.99 7.99", 1, 0.95
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["item_code"], "7898951850217")
        self.assertEqual(item["description"], "MASSA LASANHA BARILLA 2")
        self.assertEqual(item["quantity"], 1.0)
        self.assertEqual(item["unit"], "UN")

    def test_parses_weighted_item_with_merged_columns(self):
        item = parse_structured_item(
            "327360 PA0 FRANCESKG0.480KG 21,90 10.51", 32, 0.94
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["item_code"], "7360")
        self.assertEqual(item["description"], "PAO FRANCESKG")
        self.assertEqual(item["quantity"], 0.48)
        self.assertEqual(item["unit"], "KG")
        self.assertAlmostEqual(item["item_total"], 10.51)

    def test_does_not_absorb_first_description_letter_into_ean(self):
        item = parse_structured_item(
            "30 7896625210664 0UE1J0 PARM V160R 200 1UN 26.99 26.99", 30, 0.95
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["item_code"], "7896625210664")
        self.assertTrue(item["description"].startswith("QUEIJO"))

    def test_accepts_three_digit_plu_and_merged_prices(self):
        item = parse_structured_item(
            "11246 TOMATE SALADA KG 0.825 KG 6.89 5.68", 11, 0.95
        )
        self.assertIsNotNone(item)
        self.assertEqual(item["item_code"], "246")
        self.assertEqual(item["unit"], "KG")
        self.assertAlmostEqual(item["item_total"], 5.68)

        repeated = parse_structured_item(
            "06 7891097000799106 BATAVO 450G INTEGRA 1UM9.299.29", 6, 0.9
        )
        self.assertIsNotNone(repeated)
        self.assertEqual(repeated["item_code"], "7891097000799")
        self.assertEqual(repeated["unit"], "UN")
        self.assertAlmostEqual(repeated["item_total"], 9.29)


if __name__ == "__main__":
    unittest.main()
