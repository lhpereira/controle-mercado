from __future__ import annotations

import re

CATEGORIES = [
    "Bebidas sem álcool",
    "Cervejas e vinhos",
    "Laticínios e frios",
    "Carnes e ovos",
    "Padaria e doces",
    "Mercearia",
    "Hortifruti",
    "Limpeza",
    "Higiene e papel",
    "Casa e utilidades",
    "Outros",
]

RULES = [
    ("Cervejas e vinhos", r"BEB CERV|CERVEJ|CERV |VINHO|VN CHI|ESPUMANTE"),
    (
        "Laticínios e frios",
        r"LEITE|QJ |QJO|QUEIJO|MUSS|PRESUNTO|MORTADELA|MANT |REQ |REQUEI|CR LEIT|BEB LAC|IOGUR",
    ),
    ("Carnes e ovos", r"CARNE|FRANGO|PEITO|COXA|LINGUI|OVOS?|BOVIN|SUIN"),
    ("Padaria e doces", r"PAO |PÃO |BOLINHO|BISC|CHOC|DOCE|MARM |GOIAB|BOLO"),
    ("Limpeza", r"LAVA ROUP|TIRA MAN|DETERG|DESINF|SABAO|SABÃO|AMACIANTE|ST LUX"),
    ("Bebidas sem álcool", r"AGUA |ÁGUA |SUCO|REFRIG|BEB (?!CERV)|NECTAR|NÉCTAR"),
    (
        "Mercearia",
        r"ARROZ|FEIJAO|FEIJÃO|MAC |MASSA|MOLHO|TOMATE PELADO|AZEITE|OLEO|ÓLEO|FARINHA|ACUCAR|AÇÚCAR|SAL ",
    ),
    ("Hortifruti", r"MACA |MAÇÃ|BANANA|TOMATE|CEBOLA|ALFACE|BATATA|MAMÃO|LARANJA"),
    ("Higiene e papel", r"PH |PAPEL|SHAMP|SABON|DESOD|CREME DENT|ABSORV"),
    ("Casa e utilidades", r"PALITO|SACO|PILHA|FOSFORO|FÓSFORO|GUARDANAPO"),
]


def infer_category(description: str | None) -> str:
    value = (description or "").upper()
    for category, pattern in RULES:
        if re.search(pattern, value):
            return category
    return "Outros"

