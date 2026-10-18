"""Golden-set evaluation (plano de testes §6 and §13).

    python -m basketiq.eval.golden --dir tests/golden/synthetic --baseline tests/golden/baseline.json
    python -m basketiq.eval.golden --dir ../Receipts --ocr          # real samples, kept out of git

A case is either a folder with `document.<ext>` + `expected.json` (synthetic set) or a file
`<name>.<ext>` with a sibling `<name>.expected.json` (real samples). Metrics are reported per
merchant; with --baseline the run fails if any metric regresses.
"""

from __future__ import annotations

import argparse
import difflib
import json
import mimetypes
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from basketiq.domain.confidence import Thresholds
from basketiq.domain.normalization import normalize_key
from basketiq.pipeline.ocr import NoOcr, OcrEngine
from basketiq.pipeline.orchestrator import PipelineDeps, PipelineRequest, run_pipeline
from basketiq.pipeline.schema import ParsedLine

DOC_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".heic")
REGRESSION_TOLERANCE = 1e-9


@dataclass
class Case:
    name: str
    document: Path
    expected: dict[str, Any]


@dataclass
class MerchantStats:
    documents: int = 0
    merchant_ok: int = 0
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
    failures: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        def ratio(a: int, b: int) -> float | None:
            return round(a / b, 4) if b else None

        return {
            "documents": self.documents,
            "merchant_accuracy": ratio(self.merchant_ok, self.documents),
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
            "failures": self.failures,
        }


def discover(root: Path) -> list[Case]:
    cases: list[Case] = []
    for exp in sorted(root.rglob("expected.json")):
        docs = [p for p in exp.parent.iterdir() if p.suffix.lower() in DOC_SUFFIXES]
        if docs:
            cases.append(Case(exp.parent.name, docs[0], json.loads(exp.read_text(encoding="utf-8"))))
    for exp in sorted(root.rglob("*.expected.json")):
        stem = exp.name[: -len(".expected.json")]
        docs = [p for p in exp.parent.iterdir() if p.stem == stem and p.suffix.lower() in DOC_SUFFIXES]
        if docs:
            cases.append(
                Case(f"{exp.parent.name}/{stem}", docs[0], json.loads(exp.read_text(encoding="utf-8")))
            )
    return cases


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
            score = difflib.SequenceMatcher(
                None, normalize_key(exp["raw_description"]), normalize_key(line.raw_description)
            ).ratio()
            if line.line_total_cents == exp.get("line_total_cents"):
                score += 0.5
            if score > best_score:
                best, best_score = i, score
        if best is not None and best_score >= 1.2:  # same amount and a similar description
            used.add(best)
            pairs.append((exp, extracted[best]))
    return pairs


def evaluate_case(case: Case, ocr: OcrEngine, stats: dict[str, MerchantStats]) -> dict[str, Any]:
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
    out = run_pipeline(PipelineRequest(data=data, declared_mime=mime), deps)
    parsed = out.parsed
    s = stats[exp.get("merchant", "UNKNOWN")]
    s.documents += 1
    result: dict[str, Any] = {"case": case.name, "status": out.status, "route": out.route}

    def fail(msg: str) -> None:
        s.failures.append(f"{case.name}: {msg}")

    got_merchant = parsed.merchant if parsed else None
    if got_merchant == exp.get("merchant"):
        s.merchant_ok += 1
    else:
        fail(f"merchant {got_merchant} != {exp.get('merchant')}")
    if out.route == exp.get("route"):
        s.route_ok += 1
    else:
        fail(f"route {out.route} != {exp.get('route')}")
    if exp.get("status"):
        s.status_checked += 1
        if out.status == exp["status"]:
            s.status_ok += 1
        else:
            fail(f"status {out.status} != {exp['status']}")
    if exp.get("purchase_at"):
        s.date_checked += 1
        got = parsed.purchase_at if parsed else None
        if got is not None and got == datetime.fromisoformat(exp["purchase_at"]):
            s.date_ok += 1
        else:
            fail(f"purchase_at {got} != {exp['purchase_at']}")
    if "total_cents" in exp:
        s.total_checked += 1
        if parsed and parsed.total_cents == exp["total_cents"]:
            s.total_ok += 1
        else:
            fail(f"total {parsed.total_cents if parsed else None} != {exp['total_cents']}")
    if exp.get("review_codes") is not None:
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
        if len(pairs) < len(expected_lines):
            fail(f"matched {len(pairs)}/{len(expected_lines)} lines")
        for e, g in pairs:
            s.price_abs_error += abs((g.line_total_cents or 0) - e["line_total_cents"])
            for key, got_value in (
                ("quantity", g.quantity),
                ("unit", g.unit),
                ("unit_price_cents", g.unit_price_cents),
                ("discount_cents", g.discount_cents),
            ):
                if key not in e:
                    continue
                s.field_checked += 1
                want = Decimal(e[key]) if key == "quantity" else e[key]
                if got_value == want:
                    s.field_ok += 1
                else:
                    fail(f"{e['raw_description']}: {key} {got_value} != {want}")
    return result


def run(dirs: list[Path], use_ocr: bool, engine: OcrEngine | None = None) -> dict[str, Any]:
    """`engine` lets callers that run several sets share one OCR engine (its models load once)."""
    ocr: OcrEngine = engine or NoOcr()
    if use_ocr and engine is None:
        from basketiq.config import get_settings
        from basketiq.pipeline.ocr import build_ocr_engine

        ocr = build_ocr_engine(get_settings())
    stats: dict[str, MerchantStats] = defaultdict(MerchantStats)
    results = []
    skipped = []
    for root in dirs:
        for case in discover(root):
            if case.expected.get("requires_ocr") and not use_ocr:
                skipped.append(case.name)
                continue
            results.append(evaluate_case(case, ocr, stats))
    return {
        "per_merchant": {m: st.summary() for m, st in sorted(stats.items())},
        "cases": results,
        "skipped": skipped,
    }


def regressions(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    problems = []
    lower_is_better = {"price_mae_cents"}
    for merchant, metrics in baseline.get("per_merchant", {}).items():
        current = report["per_merchant"].get(merchant)
        if current is None:
            problems.append(f"{merchant}: missing from this run")
            continue
        for key, base in metrics.items():
            if not isinstance(base, int | float) or key == "documents":
                continue
            now = current.get(key)
            if now is None:
                problems.append(f"{merchant}.{key}: no value (baseline {base})")
            elif key in lower_is_better and now > base + REGRESSION_TOLERANCE:
                problems.append(f"{merchant}.{key}: {now} > baseline {base}")
            elif key not in lower_is_better and now < base - REGRESSION_TOLERANCE:
                problems.append(f"{merchant}.{key}: {now} < baseline {base}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", action="append", type=Path, required=True)
    parser.add_argument("--ocr", action="store_true", help="also run cases that need local OCR")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    report = run(args.dir, args.ocr)
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    print(json.dumps(report["per_merchant"], indent=2, ensure_ascii=False))
    if args.baseline and args.write_baseline:
        slim = {
            "per_merchant": {
                m: {k: v for k, v in s.items() if k != "failures"} for m, s in report["per_merchant"].items()
            }
        }
        args.baseline.write_text(json.dumps(slim, indent=2) + "\n", encoding="utf-8")
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
