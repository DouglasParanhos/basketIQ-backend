"""Package size from the description (spec 010, D9). Loose descriptions written for the tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from basketiq.catalog import DEFAULT_PATH, load_catalog
from basketiq.domain.size import Size, extract_size

PLAUSIBLE = load_catalog(DEFAULT_PATH)[0].plausible_sizes


def size(description: str) -> tuple[str, str] | None:
    s = extract_size(description, PLAUSIBLE)
    return (str(s.value), s.unit) if s else None


@pytest.mark.req("F-60")
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("LEITE UHT GORDO, 1L", ("1", "l")),
        ("ÁGUA SEM GÁS PD 6L", ("6", "l")),
        ("BATATA BRANCA 2,5KG -", ("2.5", "kg")),
        ("SORTIDO DE PÃO, 500 G", ("0.5", "kg")),
        ("BAT QUAR ASSAR PD1KG", ("1", "kg")),
        ("TEMPERO LIMAO 200ML", ("0.2", "l")),
        ("emb. 75 cl", ("0.75", "l")),
        ("Garrafa de 750 ml", ("0.75", "l")),
        # Multipacks N×M (D9): the whole package.
        ("emb. 6 x 1 lt", ("6", "l")),
        ("emb. 8 x 1 lt", ("8", "l")),
        ("3x200 ML", ("0.6", "l")),
        ("B. MATINAIS PD 6X50G", ("0.3", "kg")),
        ("CROQ CARN PD 10X32G", ("0.32", "kg")),
        # Counts.
        ("OVO L SOLO PD 12UN", ("12", "un")),
        ("24 OVOS FRESCOS", ("24", "un")),
        ("emb. 24 un", ("24", "un")),
        ("ATUM AZEITE X4", ("4", "un")),
        ("Uma Dúzia", ("12", "un")),
        ("1/2 Dúzia", ("6", "un")),
        ("Meia Dúz", ("6", "un")),
        ("Duas Dúzias", ("24", "un")),
        # Units of use.
        ("PAPEL HIGIENICO 12 ROLOS", ("12", "rolo")),
        ("DETERGENTE ROUPA 40 DOSES", ("40", "dose")),
    ],
)
def test_the_size_is_read_from_the_description(description: str, expected: tuple[str, str]) -> None:
    assert size(description) == expected


@pytest.mark.req("F-61")
@pytest.mark.parametrize(
    "description",
    [
        # Cut descriptions (a number without a unit): nothing, never «1 kg» by guess.
        "ARROZ EXTRA CAROLINO 1",
        "GRÃO-DE-BICO FRASCO,40",
        "IOGURTE GREGO 10%, 1",
        # N×M without a unit.
        "COXINHAS F F PD 9X40",
        "QJ FRS MAT LGHT 3X72",
        "IOG LIQ ACTIVIA4X155",
        # No size at all.
        "SALADA ICEBERGUE",
        "PAPEL HIGIENICO 4 FOLHAS",
        # Two different sizes in the same description.
        "PACK 500G + 1KG",
        # Implausible (out of the catalogue's range).
        "SACOS DO LIXO 50L",
        "AGUA 0ML",
    ],
)
def test_no_size_is_given_when_it_is_missing_ambiguous_or_implausible(description: str) -> None:
    assert size(description) is None


@pytest.mark.req("F-61")
def test_the_same_size_written_twice_is_one_size() -> None:
    assert extract_size("LEITE 1L UHT 1 L", PLAUSIBLE) == Size(Decimal(1), "l")
