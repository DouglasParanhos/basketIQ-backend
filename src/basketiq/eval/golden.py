"""Golden-set evaluation (plano de testes §6 and §13).

    python -m basketiq.eval.golden --dir tests/golden/synthetic --baseline tests/golden/baseline.json
    python -m basketiq.eval.golden --dir ../Receipts --ocr          # real samples, kept out of git
    python -m basketiq.eval.golden --dir ../Receipts --ocr --unseen # generic parser on every receipt

A case is either a folder with `document.<ext>` + `expected.json` (synthetic set) or a file
`<name>.<ext>` with a sibling `<name>.expected.json` (real samples). Metrics are reported per
merchant, per processing route and overall; with --baseline the run fails if any metric regresses.

--unseen forces the generic parser (what a layout nobody calibrated gets); --force-unknown also makes the
classifier answer UNKNOWN. Documents under a `_blind` folder are left out unless --blind is given.
"""

from __future__ import annotations

import argparse
import difflib
import json
import mimetypes
import sys
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from basketiq.domain.confidence import Thresholds
from basketiq.domain.normalization import normalize_key
from basketiq.domain.status import Merchant, ReceiptStatus, SourceType
from basketiq.pipeline.classify import Classification
from basketiq.pipeline.ocr import NoOcr, OcrEngine
from basketiq.pipeline.orchestrator import PipelineDeps, PipelineRequest, run_pipeline
from basketiq.pipeline.parsers import GenericPtParser
from basketiq.pipeline.schema import DocumentText, ParsedLine

DOC_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".heic")
BLIND_DIR = "_blind"
REGRESSION_TOLERANCE = 1e-9
DATE_TOLERANCE = timedelta(minutes=5)  # receipts print several clocks (header, card terminal)
LOOSE_MATCH = 0.6  # description similarity above which a misread line still counts as "the same line"
LOWER_IS_BETTER = {"price_mae_cents", "corrections_per_note", "unreviewed_wrong"}
NOT_BASELINED = {  # noisy, per-case or reported apart from the parser (QR), never compared
    "failures",
    "time_mean_ms",
    "time_max_ms",
    "qr_read_rate",
    "qr_date_accuracy",
    "qr_total_accuracy",
}
UNSEEN_CAVEAT = (
    "Small, biased sample: the calibrated parsers extend the generic one and were tuned on these same "
    "receipts, so these numbers are optimistic. They show that the mechanics work and serve as a "
    "regression control; they do not prove that unseen shops are read well. unreviewed_wrong is 0 by "
    "construction (the generic parser caps confidence at 0.6, so every receipt goes to review) and only "
    "becomes informative once confidence can be earned."
)


@dataclass
class Case:
    name: str
    document: Path
    expected: dict[str, Any]


@dataclass
class MerchantStats:
    documents: int = 0
    merchant_ok: int = 0
    merchant_checked: int = 0
    date_ok: int = 0
    date_checked: int = 0
    total_ok: int = 0
    total_checked: int = 0
    status_ok: int = 0
    status_checked: int = 0
    route_ok: int = 0
    expected_lines: int = 0
    extracted_lines: int = 0
    matched_lines: int = 0
    field_ok: int = 0
    field_checked: int = 0
    price_abs_error: int = 0
    review_flag_ok: int = 0
    review_flag_checked: int = 0
    corrections: int = 0  # edits a person would make, see _line_edits
    qr_read: int = 0  # notes whose fiscal QR code was read
    qr_date_ok: int = 0
    qr_total_ok: int = 0  # the QR total held against the read one without a reason to review
    unreviewed_wrong: int = 0  # needed corrections but was not sent to review
    time_total_ms: int = 0
    time_max_ms: int = 0
    failures: list[str] = field(default_factory=list)

    def merge(self, other: MerchantStats) -> None:
        for f in fields(self):
            mine, theirs = getattr(self, f.name), getattr(other, f.name)
            if f.name == "time_max_ms":
                setattr(self, f.name, max(mine, theirs))
            elif isinstance(mine, list):
                mine.extend(theirs)
            else:
                setattr(self, f.name, mine + theirs)

    def summary(self) -> dict[str, Any]:
        def ratio(a: int, b: int) -> float | None:
            return round(a / b, 4) if b else None

        return {
            "documents": self.documents,
            "merchant_accuracy": ratio(self.merchant_ok, self.merchant_checked),
            "date_accuracy": ratio(self.date_ok, self.date_checked),
            "total_accuracy": ratio(self.total_ok, self.total_checked),
            "status_accuracy": ratio(self.status_ok, self.status_checked),
            "route_accuracy": ratio(self.route_ok, self.documents),
            "line_recall": ratio(self.matched_lines, self.expected_lines),
            "line_precision": ratio(self.matched_lines, self.extracted_lines),
            "field_accuracy": ratio(self.field_ok, self.field_checked),
            "price_mae_cents": round(self.price_abs_error / self.matched_lines, 2)
            if self.matched_lines
            else None,
            "review_flag_accuracy": ratio(self.review_flag_ok, self.review_flag_checked),
            "corrections_per_note": round(self.corrections / self.documents, 2) if self.documents else None,
            "unreviewed_wrong": self.unreviewed_wrong,
            "qr_read_rate": round(self.qr_read / self.documents, 4) if self.documents else None,
            "qr_date_accuracy": round(self.qr_date_ok / self.qr_read, 4) if self.qr_read else None,
            "qr_total_accuracy": round(self.qr_total_ok / self.qr_read, 4) if self.qr_read else None,
            "time_mean_ms": round(self.time_total_ms / self.documents) if self.documents else None,
            "time_max_ms": self.time_max_ms,
            "failures": self.failures,
        }


def _wanted(path: Path, root: Path, blind: bool) -> bool:
    return (BLIND_DIR in path.relative_to(root).parts) == blind


def discover(root: Path, blind: bool = False) -> list[Case]:
    cases: list[Case] = []
    for exp in sorted(root.rglob("expected.json")):
        docs = [p for p in exp.parent.iterdir() if p.suffix.lower() in DOC_SUFFIXES]
        if docs and _wanted(exp, root, blind):
            cases.append(Case(exp.parent.name, docs[0], json.loads(exp.read_text(encoding="utf-8"))))
    for exp in sorted(root.rglob("*.expected.json")):
        stem = exp.name[: -len(".expected.json")]
        docs = [p for p in exp.parent.iterdir() if p.stem == stem and p.suffix.lower() in DOC_SUFFIXES]
        if docs and _wanted(exp, root, blind):
            cases.append(
                Case(f"{exp.parent.name}/{stem}", docs[0], json.loads(exp.read_text(encoding="utf-8")))
            )
    return cases


def unlabelled(root: Path, cases: list[Case], blind: bool = False) -> list[str]:
    """Documents under `root` that no case covers: they would otherwise be skipped without a word."""
    covered = {c.document for c in cases}
    return sorted(
        f"{p.parent.name}/{p.name}"
        for p in root.rglob("*")
        if p.suffix.lower() in DOC_SUFFIXES and p not in covered and _wanted(p, root, blind)
    )


def _match_lines(
    expected: list[dict[str, Any]], extracted: list[ParsedLine]
) -> list[tuple[dict[str, Any], ParsedLine]]:
    pairs: list[tuple[dict[str, Any], ParsedLine]] = []
    used: set[int] = set()
    for exp in expected:
        best, best_score = None, 0.0
        for i, line in enumerate(extracted):
            if i in used:
                continue
            score = _similarity(exp, line)
            if line.line_total_cents == exp.get("line_total_cents"):
                score += 0.5
            if score > best_score:
                best, best_score = i, score
        if best is not None and best_score >= 1.2:  # same amount and a similar description
            used.add(best)
            pairs.append((exp, extracted[best]))
    return pairs


def _compare_fields(e: dict[str, Any], g: ParsedLine) -> Iterator[tuple[str, Any, Any]]:
    """(field, got, wanted) for each detail the expected line states."""
    for key, got in (
        ("quantity", g.quantity),
        ("unit", g.unit),
        ("unit_price_cents", g.unit_price_cents),
        ("discount_cents", g.discount_cents),
    ):
        if key in e:
            yield key, got, Decimal(e[key]) if key == "quantity" else e[key]


def _similarity(e: dict[str, Any], g: ParsedLine) -> float:
    return difflib.SequenceMatcher(
        None, normalize_key(e["raw_description"]), normalize_key(g.raw_description)
    ).ratio()


def _line_edits(
    expected: list[dict[str, Any]],
    extracted: list[ParsedLine],
    pairs: list[tuple[dict[str, Any], ParsedLine]],
) -> int:
    """Edits a person would make to the extracted lines: one per misread line (however many of its
    fields are wrong), one per missing line to add and one per extra line to remove."""
    edits = sum(
        1
        for e, g in pairs
        if _similarity(e, g) < 1 or any(got != want for _, got, want in _compare_fields(e, g))
    )
    paired_expected = {id(e) for e, _ in pairs}
    paired_extracted = {id(g) for _, g in pairs}
    left_over = [g for g in extracted if id(g) not in paired_extracted]
    for e in expected:
        if id(e) in paired_expected:
            continue
        edits += 1  # a misread line if some leftover is "the same line", otherwise a missing one
        scored = [
            (_similarity(e, g) + (1.0 if g.line_total_cents == e.get("line_total_cents") else 0.0), i)
            for i, g in enumerate(left_over)
        ]
        best = max(scored, default=None)
        if best is not None and best[0] >= LOOSE_MATCH:
            del left_over[best[1]]
    return edits + len(left_over)


def _classify_unknown(_doc: DocumentText, _hint: Merchant | None, _nif: str | None) -> Classification:
    return Classification(merchant=Merchant.UNKNOWN, confidence=0.0, signals=[])


def _no_qr(_data: bytes, _kind: SourceType, _budget: float) -> None:
    return None


def evaluate_case(
    case: Case, ocr: OcrEngine, unseen: bool = False, force_unknown: bool = False, no_qr: bool = False
) -> tuple[dict[str, Any], MerchantStats]:
    """Runs one document and returns its result plus its own stats, to be merged by the caller."""
    exp = case.expected
    data = case.document.read_bytes()
    mime = mimetypes.guess_type(case.document.name)[0] or "application/octet-stream"
    deps = PipelineDeps(
        ocr=ocr,
        resolver=None,
        external=None,
        external_budget_remaining_cents=0,
        thresholds_for=lambda _m: Thresholds(),
        merchant_enabled=lambda _m: True,
        max_bytes=20 * 1024 * 1024,
        max_pages=5,
    )
    if unseen:
        deps.parser_factory = GenericPtParser
    if force_unknown:
        deps.classifier = _classify_unknown
    if no_qr:
        deps.qr_reader = _no_qr
    out = run_pipeline(PipelineRequest(data=data, declared_mime=mime), deps)
    parsed = out.parsed
    s = MerchantStats(documents=1)
    s.time_total_ms = s.time_max_ms = sum(out.stage_ms.values())
    result: dict[str, Any] = {
        "case": case.name,
        "merchant": exp.get("merchant", "UNKNOWN"),
        "status": out.status,
        "route": out.route,
    }

    def fail(msg: str) -> None:
        s.failures.append(f"{case.name}: {msg}")

    if not force_unknown:  # the classifier is bypassed, so there is nothing to check
        s.merchant_checked += 1
        got_merchant = parsed.merchant if parsed else None
        if got_merchant == exp.get("merchant"):
            s.merchant_ok += 1
        else:
            fail(f"merchant {got_merchant} != {exp.get('merchant')}")
    if out.route == exp.get("route"):
        s.route_ok += 1
    else:
        fail(f"route {out.route} != {exp.get('route')}")
    # The expected status and review codes describe the calibrated path; the generic parser caps
    # confidence at 0.6, so in unseen mode every receipt goes to review by construction.
    if exp.get("status") and not unseen:
        s.status_checked += 1
        if out.status == exp["status"]:
            s.status_ok += 1
        else:
            fail(f"status {out.status} != {exp['status']}")
    if exp.get("purchase_at"):
        s.date_checked += 1
        got = parsed.purchase_at if parsed else None
        if got is not None and abs(got - datetime.fromisoformat(exp["purchase_at"])) <= DATE_TOLERANCE:
            s.date_ok += 1
        else:
            s.corrections += 1
            fail(f"purchase_at {got} != {exp['purchase_at']}")
    if "total_cents" in exp:
        s.total_checked += 1
        if parsed and parsed.total_cents == exp["total_cents"]:
            s.total_ok += 1
        else:
            s.corrections += 1
            fail(f"total {parsed.total_cents if parsed else None} != {exp['total_cents']}")
    if exp.get("review_codes") is not None and not unseen:
        s.review_flag_checked += 1
        codes = {r.code for r in out.evaluation.reasons} if out.evaluation else set()
        if set(exp["review_codes"]) <= codes:
            s.review_flag_ok += 1
        else:
            fail(f"review codes {sorted(codes)} missing {exp['review_codes']}")

    expected_lines = exp.get("lines", [])
    extracted = parsed.lines if parsed else []
    if expected_lines:
        s.expected_lines += len(expected_lines)
        s.extracted_lines += len(extracted)
        pairs = _match_lines(expected_lines, extracted)
        s.matched_lines += len(pairs)
        s.corrections += _line_edits(expected_lines, extracted, pairs)
        if len(pairs) < len(expected_lines):
            fail(f"matched {len(pairs)}/{len(expected_lines)} lines")
        for e, g in pairs:
            s.price_abs_error += abs((g.line_total_cents or 0) - e["line_total_cents"])
            for key, got_value, want in _compare_fields(e, g):
                s.field_checked += 1
                if got_value == want:
                    s.field_ok += 1
                else:
                    fail(f"{e['raw_description']}: {key} {got_value} != {want}")
    s.unreviewed_wrong = int(out.status == ReceiptStatus.COMPLETED and s.corrections > 0)
    if out.qr is not None:
        s.qr_read += 1
        s.qr_date_ok += int(out.qr.doc_date.isoformat() == exp.get("purchase_at", "")[:10])
        s.qr_total_ok += int(not any(note["field"] == "total" for note in out.qr_notes))
    result |= {
        "corrections": s.corrections,
        "time_ms": s.time_total_ms,
        "qr_ms": out.stage_ms.get("qr"),
        "qr_read": out.qr is not None,
    }
    return result, s


def run(
    dirs: list[Path],
    use_ocr: bool,
    engine: OcrEngine | None = None,
    *,
    unseen: bool = False,
    force_unknown: bool = False,
    blind: bool = False,
    no_qr: bool = False,
) -> dict[str, Any]:
    """`engine` lets callers that run several sets share one OCR engine (its models load once)."""
    ocr: OcrEngine = engine or NoOcr()
    if use_ocr and engine is None:
        from basketiq.config import get_settings
        from basketiq.pipeline.ocr import build_ocr_engine

        ocr = build_ocr_engine(get_settings())
    per_merchant: dict[str, MerchantStats] = defaultdict(MerchantStats)
    per_route: dict[str, MerchantStats] = defaultdict(MerchantStats)
    overall = MerchantStats()
    results = []
    skipped = []
    without_expected = []
    for root in dirs:
        cases = discover(root, blind)
        without_expected += unlabelled(root, cases, blind)
        for case in cases:
            if case.expected.get("requires_ocr") and not use_ocr:
                skipped.append(case.name)
                continue
            result, stats = evaluate_case(case, ocr, unseen or force_unknown, force_unknown, no_qr)
            results.append(result)
            for bucket in (per_merchant[result["merchant"]], per_route[str(result["route"])], overall):
                bucket.merge(stats)
    return {
        "per_merchant": {m: st.summary() for m, st in sorted(per_merchant.items())},
        "per_route": {r: st.summary() for r, st in sorted(per_route.items())},
        "overall": overall.summary(),
        "cases": results,
        "skipped": skipped,
        "unlabelled": without_expected,
    }


def regressions(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    problems = []
    entries = [
        (label, metrics, report.get(section, {}).get(label))
        for section in ("per_merchant", "per_route")
        for label, metrics in baseline.get(section, {}).items()
    ]
    if "overall" in baseline:
        entries.append(("overall", baseline["overall"], report["overall"]))
    for label, metrics, current in entries:
        if current is None:
            problems.append(f"{label}: missing from this run")
            continue
        for key, base in metrics.items():
            if not isinstance(base, int | float) or key == "documents" or key in NOT_BASELINED:
                continue
            now = current.get(key)
            if now is None:
                problems.append(f"{label}.{key}: no value (baseline {base})")
            elif key in LOWER_IS_BETTER and now > base + REGRESSION_TOLERANCE:
                problems.append(f"{label}.{key}: {now} > baseline {base}")
            elif key not in LOWER_IS_BETTER and now < base - REGRESSION_TOLERANCE:
                problems.append(f"{label}.{key}: {now} < baseline {base}")
    return problems


def _slim(section: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in section.items() if k not in NOT_BASELINED}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", action="append", type=Path, required=True)
    parser.add_argument("--ocr", action="store_true", help="also run cases that need local OCR")
    parser.add_argument("--unseen", action="store_true", help="force the generic parser on every receipt")
    parser.add_argument(
        "--force-unknown",
        action="store_true",
        help="also classify every receipt as UNKNOWN (implies --unseen)",
    )
    parser.add_argument("--no-qr", action="store_true", help="skip the fiscal QR reader: the parser alone")
    parser.add_argument("--blind", action="store_true", help=f"run only the documents under {BLIND_DIR}/")
    parser.add_argument(
        "--meta", action="append", default=[], metavar="KEY=VALUE", help="kept in the baseline"
    )
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    unseen = args.unseen or args.force_unknown
    report = run(
        args.dir,
        args.ocr,
        unseen=unseen,
        force_unknown=args.force_unknown,
        blind=args.blind,
        no_qr=args.no_qr,
    )
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    meta = {
        "mode": "unseen+unknown" if args.force_unknown else "unseen" if unseen else "calibrated",
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
        "documents": report["overall"]["documents"],
        "qr": "off" if args.no_qr else "on",
        **dict(item.split("=", 1) for item in args.meta),
    }
    if unseen:
        meta["caveat"] = UNSEEN_CAVEAT
        print(f"NOTE: {UNSEEN_CAVEAT}\n")
    if report["unlabelled"]:
        print(f"WARNING: no .expected.json for {report['unlabelled']}; not measured", file=sys.stderr)
    print(json.dumps(report["per_merchant"], indent=2, ensure_ascii=False))
    if unseen:
        print(json.dumps({k: report[k] for k in ("per_route", "overall")}, indent=2, ensure_ascii=False))
    if args.baseline and args.write_baseline:
        slim = {
            "meta": meta,
            "per_merchant": {m: _slim(s) for m, s in report["per_merchant"].items()},
            "per_route": {r: _slim(s) for r, s in report["per_route"].items()},
            "overall": _slim(report["overall"]),
        }
        args.baseline.write_text(json.dumps(slim, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"baseline written to {args.baseline}")
        return
    if args.baseline:
        problems = regressions(report, json.loads(args.baseline.read_text(encoding="utf-8")))
        if problems:
            print("REGRESSIONS:\n  " + "\n  ".join(problems))
            sys.exit(1)
        print("no regressions against baseline")


if __name__ == "__main__":
    main()
