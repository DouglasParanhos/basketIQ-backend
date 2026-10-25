from __future__ import annotations

import pytest

from basketiq.domain.status import ProcessingRoute
from basketiq.pipeline.schema import DocumentText, TextLine, TextToken
from basketiq.pipeline.stitch import join_documents


def doc(*texts: str, conf: float = 0.95) -> DocumentText:
    lines = [
        TextLine(tokens=[TextToken(text=t, x0=0, x1=100, y0=i * 20, y1=i * 20 + 15, confidence=conf)])
        for i, t in enumerate(texts)
    ]
    return DocumentText(lines=lines, route=ProcessingRoute.LOCAL_OCR, engine="test")


def texts(joined: DocumentText) -> list[str]:
    return [line.text for line in joined.lines]


@pytest.mark.req("RF-03")
def test_a_single_photo_is_returned_as_it_is() -> None:
    one = doc("A 1,00", "B 2,00")
    joined, notes = join_documents([one])
    assert joined is one and notes == []


@pytest.mark.req("RF-03")
def test_the_repeated_run_is_removed_once() -> None:
    first = doc("LEITE 0,79", "PAO 1,29", "ARROZ 1,10", "OVOS 2,50", "SUMO 1,99")
    second = doc("ARROZ 1,10", "OVOS 2,50", "SUMO 1,99", "BANANA 1,14", "TOTAL 8,81")
    joined, notes = join_documents([first, second])
    assert texts(joined) == [
        "LEITE 0,79",
        "PAO 1,29",
        "ARROZ 1,10",
        "OVOS 2,50",
        "SUMO 1,99",
        "BANANA 1,14",
        "TOTAL 8,81",
    ]
    assert notes == [{"photo": 1, "overlap": 3, "removed": ["ARROZ 1,10", "OVOS 2,50", "SUMO 1,99"]}]


@pytest.mark.req("RF-03")
def test_two_readings_of_the_same_line_still_match() -> None:
    first = doc("LEITE 0,79 B", "ARROZ AGULHA 1KG 1,10 B", "OVOS FRESCOS 2,50 D")
    second = doc("ARROZ AGULHA 1KG 1.10 D", "0VOS FRESC0S 2,50 B", "SUMO 1,99 B")
    joined, notes = join_documents([first, second])
    assert len(joined.lines) == 4 and notes[0]["overlap"] == 2


@pytest.mark.req("RF-03")
def test_a_better_reading_of_a_repeated_line_replaces_the_worse_one() -> None:
    first = doc("LEITE 0,79", "ARROZ 1,10", "OVOS 2,50", conf=0.6)
    second = doc("ARROZ 1,10", "OVOS 2,50", "SUMO 1,99", conf=0.98)
    joined, _ = join_documents([first, second])
    assert [line.confidence for line in joined.lines] == [0.6, 0.98, 0.98, 0.98]


@pytest.mark.req("RF-03")
def test_a_single_repeated_line_is_two_purchases_not_an_overlap() -> None:
    first = doc("LEITE 0,79", "PAO 1,29", "IOGURTE 0,59")
    second = doc("IOGURTE 0,59", "CAFE 3,20", "SUMO 1,99")
    joined, notes = join_documents([first, second])
    assert texts(joined).count("IOGURTE 0,59") == 2
    assert notes == [{"photo": 1, "overlap": 0, "removed": []}]


@pytest.mark.req("RF-03")
def test_same_names_with_other_prices_all_along_are_other_purchases() -> None:
    first = doc("LEITE 0,79", "PAO 1,29", "IOGURTE 0,59", "CAFE 3,20")
    second = doc("IOGURTE 0,69", "CAFE 3,50", "SUMO 1,99")
    _, notes = join_documents([first, second])
    assert notes[0]["overlap"] == 0


@pytest.mark.req("RF-03")
def test_one_misread_or_misplaced_price_does_not_hide_an_overlap() -> None:
    first = doc("LEITE 0,79", "PAO 1,29", "IOGURTE 0,59", "CAFE 3,20", "ARROZ")
    second = doc("IOGURTE 0,69", "CAFE 3,20", "ARROZ 1,10", "SUMO 1,99")
    joined, notes = join_documents([first, second])
    assert notes[0]["overlap"] == 3
    assert texts(joined) == ["LEITE 0,79", "PAO 1,29", "IOGURTE 0,59", "CAFE 3,20", "ARROZ 1,10", "SUMO 1,99"]


@pytest.mark.req("RF-03")
def test_the_reading_with_a_price_is_kept_over_the_one_without() -> None:
    first = doc("LEITE 0,79", "ARROZ", "OVOS 2,50")
    second = doc("ARROZ 1,10", "OVOS 2,50", "SUMO 1,99")
    joined, _ = join_documents([first, second])
    assert texts(joined)[1] == "ARROZ 1,10"


@pytest.mark.req("RF-03")
def test_a_line_cut_in_half_at_the_top_of_the_next_photo_is_skipped() -> None:
    first = doc("LEITE 0,79", "ARROZ 1,10", "OVOS 2,50")
    second = doc("AZ 1,", "ARROZ 1,10", "OVOS 2,50", "SUMO 1,99")
    joined, notes = join_documents([first, second])
    assert texts(joined) == ["LEITE 0,79", "ARROZ 1,10", "OVOS 2,50", "SUMO 1,99"]
    assert notes[0]["overlap"] == 2


@pytest.mark.req("RF-03")
def test_three_photos_are_joined_pair_by_pair() -> None:
    a = doc("A1 1,00", "A2 2,00", "A3 3,00")
    b = doc("A2 2,00", "A3 3,00", "B1 4,00", "B2 5,00")
    c = doc("B1 4,00", "B2 5,00", "C1 6,00")
    joined, notes = join_documents([a, b, c])
    assert texts(joined) == ["A1 1,00", "A2 2,00", "A3 3,00", "B1 4,00", "B2 5,00", "C1 6,00"]
    assert [n["photo"] for n in notes] == [1, 2] and [n["overlap"] for n in notes] == [2, 2]


@pytest.mark.req("RF-03")
def test_photos_without_lines_in_common_are_joined_and_reported() -> None:
    joined, notes = join_documents([doc("A 1,00", "B 2,00"), doc("C 3,00", "D 4,00")])
    assert texts(joined) == ["A 1,00", "B 2,00", "C 3,00", "D 4,00"]
    assert notes == [{"photo": 1, "overlap": 0, "removed": []}]


@pytest.mark.req("RF-03")
def test_a_line_cut_in_half_at_the_bottom_of_the_previous_photo_is_replaced_by_the_whole_one() -> None:
    first = doc("LEITE 0,79", "ARROZ 1,10", "OVOS 2,50", "SU 1,")
    second = doc("ARROZ 1,10", "OVOS 2,50", "SUMO 1,99", "CAFE 3,20")
    joined, notes = join_documents([first, second])
    assert texts(joined) == ["LEITE 0,79", "ARROZ 1,10", "OVOS 2,50", "SUMO 1,99", "CAFE 3,20"]
    assert notes == [{"photo": 1, "overlap": 2, "removed": ["ARROZ 1,10", "OVOS 2,50", "SU 1,"]}]


@pytest.mark.req("RF-03")
def test_cut_lines_at_both_edges() -> None:
    first = doc("A1 1,00", "A2 2,00", "A3 3,00", "A4 4,0")
    second = doc("A2 2,", "A2 2,00", "A3 3,00", "A4 4,00", "A5 5,00")
    joined, _ = join_documents([first, second])
    assert texts(joined) == ["A1 1,00", "A2 2,00", "A3 3,00", "A4 4,00", "A5 5,00"]
