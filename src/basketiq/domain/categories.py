"""Product categories of the shopping list: the groups the list is drawn in.

A product the step C catalogue knows takes the category its catalogue node declares (`list_category` in
config/catalog.yaml), normally that of its class (decided by Douglas on 2026-11-10: the
15 classes of the catalogue are the one taxonomy, and these categories follow them until the list shows the
classes themselves). Names the catalogue does not know fall back on keyword rules. A person can always correct
a category.
"""

from __future__ import annotations

from basketiq.domain.normalization import normalize_key

OTHER = "outros"

# (id, name) in the order the list shows them: alphabetical, «Outros» last.
CATEGORIES: list[tuple[str, str]] = [
    ("animais", "Animais"),
    ("bebe", "Bebé"),
    ("bebidas", "Bebidas"),
    ("charcutaria", "Charcutaria"),
    ("congelados", "Congelados"),
    ("frutas_legumes", "Frutas e legumes"),
    ("higiene", "Higiene"),
    ("laticinios_ovos", "Laticínios e ovos"),
    ("limpeza", "Limpeza"),
    ("mercearia", "Mercearia"),
    ("padaria", "Padaria"),
    ("peixaria", "Peixaria"),
    ("talho", "Talho"),
    (OTHER, "Outros"),
]
CATEGORY_IDS = [c for c, _ in CATEGORIES]
CATEGORY_NAMES = dict(CATEGORIES)

# Words of the normalised name (accents out, upper case). The first category with a match wins, so the more
# specific ones come first («PAPEL HIGIENICO» is hygiene, not groceries).
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("bebe", ("FRALDAS", "PAPAS", "LENCOS HUMIDOS")),
    ("animais", ("RACAO", "AREIA GATO", "AREIA PARA GATO")),
    (
        "higiene",
        (
            "CHAMPO",
            "GEL DE BANHO",
            "SABONETE",
            "PASTA DENTIFRICA",
            "DESODORIZANTE",
            "PAPEL HIGIENICO",
            "ESCOVA DE DENTES",
        ),
    ),
    (
        "limpeza",
        (
            "DETERGENTE",
            "LIXIVIA",
            "AMACIADOR",
            "ESPONJA",
            "DESINFETANTE",
            "PAPEL DE COZINHA",
            "SACOS DO LIXO",
            "LOICA",
        ),
    ),
    ("congelados", ("GELADO", "CONGELADO", "CONGELADOS", "PIZZA")),
    ("bebidas", ("AGUA", "SUMO", "CERVEJA", "VINHO", "REFRIGERANTE", "COCA COLA", "ICE TEA")),
    ("charcutaria", ("FIAMBRE", "PRESUNTO", "CHOURICO", "SALSICHA", "SALSICHAS", "MORTADELA", "BACON")),
    ("peixaria", ("SALMAO", "BACALHAU", "PESCADA", "CAMARAO", "DOURADA", "ROBALO", "SARDINHA", "POLVO")),
    ("talho", ("FRANGO", "PEITO", "BIFE", "CARNE", "PORCO", "LOMBO", "PERU", "VACA", "HAMBURGUER", "COXAS")),
    ("padaria", ("PAO", "BROA", "CROISSANT", "BAGUETE", "PAPO SECO")),
    (
        "laticinios_ovos",
        ("LEITE", "IOGURTE", "IOGURTES", "QUEIJO", "MANTEIGA", "OVOS", "NATAS", "REQUEIJAO", "KEFIR"),
    ),
    (
        "frutas_legumes",
        (
            "BANANA",
            "BANANAS",
            "MACA",
            "LARANJA",
            "PERA",
            "ALFACE",
            "CENOURA",
            "BATATA",
            "CEBOLA",
            "TOMATE",
            "LIMAO",
            "MORANGO",
            "UVA",
            "ALHO",
            "COUVE",
            "BROCOLOS",
            "ABACATE",
            "KIWI",
        ),
    ),
    (
        "mercearia",
        (
            "ARROZ",
            "MASSA",
            "ESPARGUETE",
            "AZEITE",
            "OLEO",
            "FARINHA",
            "ACUCAR",
            "SAL",
            "CAFE",
            "CHA",
            "CEREAIS",
            "BOLACHA",
            "BOLACHAS",
            "ATUM",
            "FEIJAO",
            "GRAO",
            "LENTILHAS",
            "MOLHO",
        ),
    ),
]


def categorize(name: str) -> str:
    """The category id of a product name; «outros» when no rule matches."""
    key = f" {normalize_key(name)} "
    for category, words in _RULES:
        if any(f" {word} " in key for word in words):
            return category
    return OTHER
