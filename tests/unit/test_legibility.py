"""F-230: an image whose products were not read well enough gets the reason `illegible`, so the person takes
another photo (the receipt is still in hand) instead of reviewing a reading that lost products."""

from __future__ import annotations

from decimal import Decimal

import pytest

from basketiq.domain.confidence import LineView, ReceiptView, Thresholds, evaluate
from basketiq.domain.status import ProcessingRoute


def view(
    totals: list[int],
    printed: int | None,
    unread: int = 0,
    route: ProcessingRoute = ProcessingRoute.LOCAL_OCR,
    edited: frozenset[str] = frozenset(),
) -> ReceiptView:
    lines = [
        LineView(f"P{i}", Decimal(1), t, 0, {}, unresolved=i < unread, edited_fields=edited)
        for i, t in enumerate(totals)
    ]
    return ReceiptView("PINGO_DOCE", None, printed, None, None, {}, lines, route=route)


def illegible(v: ReceiptView) -> bool:
    return any(r.code == "illegible" for r in evaluate(v, Thresholds()).reasons)


@pytest.mark.req("F-230")
@pytest.mark.parametrize(
    ("totals", "printed", "unread", "lost"),
    [
        ([536, 140], 676, 0, False),  # read whole
        ([216, 259, 426, 10], 910, 0, False),  # one cent off: a misread price, fixed in the review
        ([500, 400], 1000, 0, False),  # 1,00 € of 10,00 € missing: one product, fixed in the review
        ([500, 300], 1000, 0, True),  # 2,00 € of 10,00 € missing: products lost
        ([], None, 0, True),  # nothing read
        ([1249, 1249], None, 0, True),  # no total: the summary was read as products
        ([100, 100, 100, 100], 400, 1, False),  # one line in four left for review
        ([100, 100, 100, 100, 100], 500, 2, True),  # two in five
    ],
)
def test_an_image_read_without_products_or_total_is_illegible(
    totals: list[int], printed: int | None, unread: int, lost: bool
) -> None:
    assert illegible(view(totals, printed, unread)) is lost


@pytest.mark.req("F-230")
def test_only_images_are_judged_and_the_persons_fixes_clear_it() -> None:
    assert not illegible(view([500], None, route=ProcessingRoute.PDF_TEXT))  # a file: no other photo to take
    assert illegible(view([100, 100], 200, unread=2))
    assert not illegible(view([100, 100], 200, unread=2, edited=frozenset({"line_total"})))
