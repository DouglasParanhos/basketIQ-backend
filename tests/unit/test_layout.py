from __future__ import annotations

import pytest

from basketiq.pipeline.layout import group_into_lines
from basketiq.pipeline.schema import TextToken

PITCH = 37  # distance between rows, smaller than the (tilt-inflated) OCR box height
HEIGHT = 51


def token(text: str, x0: float, x1: float, row: int, slope: float, y_base: float = 1000) -> TextToken:
    """A token of `row` on a page whose text runs at `slope` (dy/dx), like a tilted photo.

    Like the real OCR, long texts report their angle well and short ones badly (here: wrong by 0.06)."""
    centre = y_base + row * PITCH + slope * (x0 + x1) / 2
    reported = slope if x1 - x0 >= 3 * HEIGHT else slope + 0.06
    return TextToken(text=text, x0=x0, x1=x1, y0=centre - HEIGHT / 2, y1=centre + HEIGHT / 2, slope=reported)


def receipt_rows(slope_for_row, repeat: int = 1) -> list[TextToken]:  # type: ignore[no-untyped-def]
    rows = [
        ("COLGATE MAX WHITE", None),
        ("X 2,68", "5,36"),
        ("ELIXIR AUCHAN 500 ML", "1,40"),
        ("3 volumes", "6,76"),
        ("TOTAL PAGO", "6,76"),
        ("Visa", "0,00"),
        ("TROCO", None),
        ("RESUMO DO IVA", None),
    ]
    tokens: list[TextToken] = []
    for i, (left, right) in enumerate(rows * repeat):
        slope = slope_for_row(i)
        tokens.append(token(left, 1200, 1500, i, slope))
        if right:
            tokens.append(token(right, 1760, 1840, i, slope))
    return tokens


def texts(tokens: list[TextToken]) -> list[str]:
    return [line.text for line in group_into_lines(tokens)]


EXPECTED = [
    "COLGATE MAX WHITE",
    "X 2,68 5,36",
    "ELIXIR AUCHAN 500 ML 1,40",
    "3 volumes 6,76",
    "TOTAL PAGO 6,76",
    "Visa 0,00",
    "TROCO",
    "RESUMO DO IVA",
]


@pytest.mark.parametrize("slope", [0.0, -0.04, 0.03])
def test_rows_are_kept_apart_on_a_tilted_photo(slope: float) -> None:
    assert texts(receipt_rows(lambda _i: slope)) == EXPECTED


def test_rows_are_kept_apart_when_the_paper_curls() -> None:
    """Amounts sit higher than their descriptions near the top and lower near the bottom."""
    got = texts(receipt_rows(lambda i: -0.04 if i < 24 else 0.03, repeat=6))
    # The rows in the transition between the two tilts are not checked, only each side of it.
    assert got[:8] == EXPECTED
    assert got[-8:] == EXPECTED


def test_reported_angles_that_do_not_match_the_rows_are_not_trusted() -> None:
    """The prices already sit on the row of their description, but the OCR reports a tilt that would
    move each one next to its neighbour: the alignment of the amounts decides, not the angle."""
    rows = [
        ("ROLO GIGANTE", "2,90"),
        ("PAPEL HIGIENICO", "3,05"),
        ("GEL COCO", "1,60"),
        ("LUVAS LATEX", "3,00"),
    ] * 6
    tokens = []
    for i, (left, right) in enumerate(rows):
        for text, x0, x1 in ((left, 1200, 1500), (right, 1760, 1840)):
            centre = 1000 + i * 46  # rows 46 apart, as on the photographed Mercadona receipt
            tokens.append(
                TextToken(text=text, x0=x0, x1=x1, y0=centre - HEIGHT / 2, y1=centre + HEIGHT / 2, slope=0.06)
            )
    got = texts(tokens)
    # The first and last rows have too few amounts around them to be judged, only the stretch in between.
    assert {f"{left} {right}" for left, right in rows[8:16]} <= set(got)


def test_flat_pdf_words_are_untouched() -> None:
    words = [
        TextToken(text=w, x0=10 + 40 * i, x1=40 + 40 * i, y0=row * 12, y1=row * 12 + 9)
        for row in range(6)
        for i, w in enumerate(["ITEM", "2", "X", "1,00"])
    ]
    assert texts(words) == ["ITEM 2 X 1,00"] * 6


# ---- commit (i): a row never holds two boxes stacked in the same column ----------------------------------


def box(text: str, x0: float, x1: float, y0: float) -> TextToken:
    return TextToken(text=text, x0=x0, x1=x1, y0=y0, y1=y0 + 25)


@pytest.mark.seen_once  # seen on 1 photo: two rows of a tilted receipt joined into one line
@pytest.mark.req("F-54")
def test_two_boxes_stacked_in_the_same_column_are_not_the_same_row() -> None:
    lines = group_into_lines(
        [
            box("BATATA BRANCA 2,5KG", 60, 490, 100),
            box("2,29", 626, 730, 100),
            box("SALSICHA PICANTE", 63, 434, 110),  # ten units lower: close enough to be joined by the tilt
            box("3,99", 628, 732, 110),
        ]
    )
    assert [line.text for line in lines] == ["BATATA BRANCA 2,5KG 2,29", "SALSICHA PICANTE 3,99"]


@pytest.mark.seen_once  # seen on 1 photo: two rows of a tilted receipt joined into one line
@pytest.mark.req("F-54")
def test_a_quantity_row_stacked_over_its_item_is_split_from_it() -> None:
    lines = group_into_lines(
        [
            box("LEITE", 88, 196, 112),
            box("6 x 0,99 EUR", 182, 385, 100),
            box("UHT GORDO. 1L", 184, 392, 112),
            box("5,94", 638, 740, 112),
        ]
    )
    assert [line.text for line in lines] == ["6 x 0,99 EUR", "LEITE UHT GORDO. 1L 5,94"]


@pytest.mark.seen_once  # seen on 1 photo: two rows of a tilted receipt joined into one line
@pytest.mark.req("F-54")
def test_boxes_side_by_side_or_the_same_row_read_twice_stay_together() -> None:
    side_by_side = group_into_lines(
        [box("LEITE", 50, 120, 100), box("UHT", 130, 200, 101), box("1,20", 600, 700, 100)]
    )
    assert [line.text for line in side_by_side] == ["LEITE UHT 1,20"]
    twice = group_into_lines([box("PAO DE FORMA", 50, 300, 100), box("PAO DE FORMA", 52, 298, 102)])
    assert len(twice) == 1  # two units apart: the same row, not two


@pytest.mark.seen_once  # seen on 1 photo: two rows of a tilted receipt joined into one line
@pytest.mark.req("F-54")
def test_rows_that_were_already_apart_are_untouched() -> None:
    lines = group_into_lines(
        [box("PAO", 50, 150, 100), box("1,00", 600, 700, 100), box("LEITE", 50, 150, 160)]
    )
    assert [line.text for line in lines] == ["PAO 1,00", "LEITE"]
