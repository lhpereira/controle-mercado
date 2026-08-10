import unittest

from mercado.services.parser import parse_receipt_text


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


if __name__ == "__main__":
    unittest.main()
