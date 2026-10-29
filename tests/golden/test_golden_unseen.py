"""Unseen-layout evaluation (plano de testes F-33): the generic parser alone, measured per route."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from basketiq.domain.status import ReceiptStatus
from basketiq.eval import golden
from basketiq.eval.golden import _line_edits, _match_lines, discover, regressions, run, unlabelled
from basketiq.pipeline.schema import ParsedLine

SYNTHETIC = Path(__file__).resolve().parent / "synthetic"
NEW_METRICS = {"corrections_per_note", "unreviewed_wrong", "time_mean_ms", "time_max_ms"}


def _expected(description: str, total: int, **extra: Any) -> dict[str, Any]:
    return {"raw_description": description, "line_total_cents": total, **extra}


def _line(description: str, total: int | None, **extra: Any) -> ParsedLine:
    return ParsedLine(raw_description=description, line_total_cents=total, **extra)


def _edits(expected: list[dict[str, Any]], extracted: list[ParsedLine]) -> int:
    return _line_edits(expected, extracted, _match_lines(expected, extracted))


@pytest.mark.req("F-33")
def test_unseen_mode_uses_the_generic_parser_and_sends_everything_to_review() -> None:
    calibrated = run([SYNTHETIC / "lidl_text"], use_ocr=False)
    unseen = run([SYNTHETIC / "lidl_text"], use_ocr=False, unseen=True)
    assert calibrated["cases"][0]["status"] == ReceiptStatus.COMPLETED
    assert unseen["cases"][0]["status"] == ReceiptStatus.REVIEW_REQUIRED
    assert unseen["per_merchant"]["LIDL"]["merchant_accuracy"] == 1.0
    assert unseen["per_merchant"]["LIDL"]["status_accuracy"] is None  # status is not judged in this mode


@pytest.mark.req("F-33")
def test_force_unknown_bypasses_the_classifier() -> None:
    report = run([SYNTHETIC / "lidl_text"], use_ocr=False, force_unknown=True)
    assert report["per_merchant"]["LIDL"]["merchant_accuracy"] is None  # nothing to check
    assert report["cases"][0]["status"] == ReceiptStatus.REVIEW_REQUIRED


@pytest.mark.req("F-33")
def test_report_has_per_route_overall_and_the_new_metrics() -> None:
    report = run([SYNTHETIC], use_ocr=False, unseen=True)
    assert set(report["per_route"]) == {"PDF_TEXT"}
    assert set(report["overall"]) >= NEW_METRICS
    assert report["overall"]["documents"] == sum(m["documents"] for m in report["per_merchant"].values())
    assert report["overall"]["unreviewed_wrong"] == 0  # the 0.6 cap sends every receipt to review
    assert all({"corrections", "time_ms"} <= set(case) for case in report["cases"])


@pytest.mark.req("F-33")
def test_a_misread_line_costs_one_edit_not_two() -> None:
    expected = [_expected("TOMATE CHERRY", 249)]
    assert _edits(expected, [_line("TOMATE CHERRY", 249)]) == 0
    assert _edits(expected, [_line("TOMATE CHERRY", 294)]) == 1  # wrong price
    assert _edits(expected, [_line("TOMATE CHERY", 249)]) == 1  # wrong description


@pytest.mark.req("F-33")
def test_several_wrong_fields_on_one_line_cost_one_edit() -> None:
    expected = [_expected("QUEIJO FRESCO", 179, quantity="1", unit="un", unit_price_cents=179)]
    got = _line("QUEIJO FRESCO", 179, quantity=Decimal("2"), unit="kg", unit_price_cents=90)
    assert _edits(expected, [got]) == 1


@pytest.mark.req("F-33")
def test_missing_and_extra_lines_cost_one_edit_each() -> None:
    expected = [_expected("PAO DE FORMA", 120), _expected("LEITE MEIO GORDO", 85)]
    assert _edits(expected, [_line("PAO DE FORMA", 120)]) == 1  # one to add
    extra = [_line("PAO DE FORMA", 120), _line("LEITE MEIO GORDO", 85), _line("TOTAL A PAGAR", 205)]
    assert _edits(expected, extra) == 1  # one to remove


@pytest.mark.req("F-33")
def test_regressions_cover_route_and_overall_and_ignore_timing() -> None:
    baseline = {
        "per_route": {"PDF_TEXT": {"line_recall": 0.9, "time_mean_ms": 10}},
        "overall": {"corrections_per_note": 1.0, "unreviewed_wrong": 0},
    }
    ok = {
        "per_route": {"PDF_TEXT": {"line_recall": 0.95, "time_mean_ms": 9999}},
        "overall": {"corrections_per_note": 1.0, "unreviewed_wrong": 0},
    }
    assert regressions(ok, baseline) == []
    worse = {
        "per_route": {"PDF_TEXT": {"line_recall": 0.8}},
        "overall": {"corrections_per_note": 2.0, "unreviewed_wrong": 1},
    }
    assert len(regressions(worse, baseline)) == 3


@pytest.mark.req("F-33")
def test_blind_documents_stay_out_unless_asked_and_unlabelled_ones_are_reported(tmp_path: Path) -> None:
    (tmp_path / "Lidl").mkdir()
    (tmp_path / "_blind").mkdir()
    for folder, name in (("Lidl", "seen"), ("_blind", "hidden")):
        (tmp_path / folder / f"{name}.pdf").write_bytes(b"%PDF")
        (tmp_path / folder / f"{name}.expected.json").write_text("{}")
    (tmp_path / "Lidl" / "no_labels.jpg").write_bytes(b"x")

    assert [c.name for c in discover(tmp_path)] == ["Lidl/seen"]
    assert [c.name for c in discover(tmp_path, blind=True)] == ["_blind/hidden"]
    assert unlabelled(tmp_path, discover(tmp_path)) == ["Lidl/no_labels.jpg"]
    assert unlabelled(tmp_path, discover(tmp_path, blind=True), blind=True) == []


@pytest.mark.req("F-33")
def test_purchase_time_matches_within_five_minutes(tmp_path: Path) -> None:
    source = SYNTHETIC / "lidl_text"
    expected = json.loads((source / "expected.json").read_text(encoding="utf-8"))
    printed = datetime.fromisoformat(expected["purchase_at"])

    def date_accuracy(offset: timedelta) -> float | None:
        case = tmp_path / f"case_{int(offset.total_seconds())}"
        shutil.copytree(source, case)
        expected["purchase_at"] = (printed + offset).isoformat()
        (case / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
        return run([case], use_ocr=False)["per_merchant"]["LIDL"]["date_accuracy"]

    assert date_accuracy(timedelta(minutes=5)) == 1.0  # header and card terminal clocks differ by minutes
    assert date_accuracy(timedelta(minutes=6)) == 0.0


@pytest.mark.req("F-33")
def test_no_qr_runs_the_parser_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    real = golden.run_pipeline

    def spy(request: Any, deps: Any) -> Any:
        seen.append(deps.qr_reader)
        return real(request, deps)

    monkeypatch.setattr(golden, "run_pipeline", spy)
    run([SYNTHETIC / "lidl_text"], use_ocr=False, unseen=True)
    run([SYNTHETIC / "lidl_text"], use_ocr=False, unseen=True, no_qr=True)
    assert seen[0] is not golden._no_qr
    assert seen[1] is golden._no_qr


@pytest.mark.req("F-33")
def test_qr_figures_are_reported_but_never_baselined() -> None:
    baseline = {"overall": {"qr_read_rate": 1.0, "line_recall": 0.9}}
    current = {"overall": {"qr_read_rate": 0.0, "line_recall": 0.9}}
    assert regressions(current, baseline) == []
