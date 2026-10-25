"""Join the text of several photos of one long receipt into a single document.

The user is asked to leave the last two or three products of the previous photo visible in the next one,
so the photos share a run of lines. That run is found by comparing the end of one photo with the start of
the next, line by line, tolerating the small differences between two readings of the same line. Only a run
of at least two consecutive lines counts: a single repeated line can be two real purchases of the same
article.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from itertools import product
from typing import TypedDict

from basketiq.pipeline.parsers.base import MONEY
from basketiq.pipeline.schema import DocumentText, TextLine

MIN_OVERLAP = 2  # lines in a row that must repeat
MAX_OVERLAP = 12
MAX_JUNK = 2  # lines cut in half at the edge of a photo that may not belong to the run
SIMILARITY = 0.85

_AMOUNT = re.compile(MONEY + r"(?!\d)")
# Characters the OCR swaps for each other; both readings of a line are folded the same way before comparing.
_LOOKALIKES = str.maketrans("015", "ols")


class JoinNote(TypedDict):
    """What was done between photo `photo` and the next one (both counted from 1)."""

    photo: int
    overlap: int  # lines found in common, 0 when the photos share none
    removed: list[str]  # repeated or cut-off lines taken out


def _key(line: TextLine) -> tuple[str, tuple[str, ...]]:
    text = unicodedata.normalize("NFKD", line.text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    amounts = tuple(a.replace(".", ",") for a in _AMOUNT.findall(text))
    words = re.sub(r"[^a-z0-9 ]", " ", _AMOUNT.sub(" ", text)).translate(_LOOKALIKES)
    words = re.sub(r"\s+", " ", words).strip()
    return re.sub(r" [a-z0-9]$", "", words), amounts  # the VAT class after the amount is not part of the line


_Key = tuple[str, tuple[str, ...]]


def _same_words(a: _Key, b: _Key) -> bool:
    words_a, words_b = a[0], b[0]
    return bool(words_a and words_b) and SequenceMatcher(None, words_a, words_b).ratio() >= SIMILARITY


def _amounts_agree(a: _Key, b: _Key) -> bool:
    """Equal amounts, or amounts missing on one side: a price the OCR put on the neighbouring row."""
    return a[1] == b[1] or not a[1] or not b[1]


def _is_the_same_run(tail: list[_Key], head: list[_Key]) -> bool:
    """The names of every line match, and at least half of the lines agree on the amounts.

    The names are what identifies a product; the amounts are read less reliably (on a photo they often
    land next to the wrong row), so a few disagreeing ones do not rule a run out."""
    pairs = list(zip(tail, head, strict=True))
    return all(_same_words(a, b) for a, b in pairs) and 2 * sum(
        _amounts_agree(a, b) for a, b in pairs
    ) >= len(pairs)


def _better_reading(first: TextLine, second: TextLine) -> TextLine:
    """Of two readings of one line, the one that has an amount, else the more confident."""
    has_first, has_second = bool(_AMOUNT.search(first.text)), bool(_AMOUNT.search(second.text))
    if has_first != has_second:
        return first if has_first else second
    return second if second.confidence > first.confidence else first


def _find_overlap(before: list[TextLine], after: list[TextLine]) -> tuple[int, int, int] | None:
    """(lines in common, cut lines at the end of `before`, cut lines at the top of `after`).

    The largest run wins, then the fewest cut lines. A photo ends and the next one starts at an arbitrary
    height, so each may have a line cut in half at that edge that the other photo shows whole."""
    keys_before, keys_after = [_key(line) for line in before], [_key(line) for line in after]
    cuts = sorted(product(range(MAX_JUNK + 1), repeat=2), key=sum)
    for run in range(min(MAX_OVERLAP, len(keys_before)), MIN_OVERLAP - 1, -1):
        for tail_cut, head_cut in cuts:
            end = len(keys_before) - tail_cut
            tail = keys_before[end - run : end]
            head = keys_after[head_cut : head_cut + run]
            if len(tail) == run == len(head) and _is_the_same_run(tail, head):
                return run, tail_cut, head_cut
    return None


def join_documents(docs: list[DocumentText]) -> tuple[DocumentText, list[JoinNote]]:
    """The documents one after the other, repeated lines removed. One note per pair of photos."""
    if len(docs) == 1:
        return docs[0], []
    lines = list(docs[0].lines)
    notes: list[JoinNote] = []
    for number, doc in enumerate(docs[1:], start=1):
        found = _find_overlap(lines, doc.lines)
        if found is None:
            notes.append(JoinNote(photo=number, overlap=0, removed=[]))
            lines.extend(doc.lines)
            continue
        run, tail_cut, head_cut = found
        cut_off = lines[len(lines) - tail_cut :]  # the next photo shows these whole
        del lines[len(lines) - tail_cut :]
        repeated = doc.lines[head_cut : head_cut + run]
        for i, later in enumerate(repeated):  # keep the better reading of every repeated line
            lines[len(lines) - run + i] = _better_reading(lines[len(lines) - run + i], later)
        notes.append(
            JoinNote(photo=number, overlap=run, removed=[line.text for line in [*repeated, *cut_off]])
        )
        lines.extend(doc.lines[head_cut + run :])
    first = docs[0]
    return (
        DocumentText(
            lines=lines,
            route=first.route,
            engine=first.engine,
            page_count=sum(doc.page_count for doc in docs),
        ),
        notes,
    )
