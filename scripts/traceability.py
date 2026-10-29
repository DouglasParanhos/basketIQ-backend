"""Requirement → test traceability matrix (constitution, principle VI).

    python scripts/traceability.py            # print the matrix, exit 1 on gaps
    python scripts/traceability.py --out reports/traceability.md

Sources:
- tests/**/*.py: `@pytest.mark.req("ID")` decorators above `def test_*` (several per test allowed);
- specs/*/spec.md: every id cited, plus delegation lines of the form
  `**Verificado no Frontend**: F-06, F-21` for scenarios verified in the Frontend repository.

Fails when a functional scenario of the test plan (F-01…F-32 with F-03a, F-15a/b, F-22a/b) has
neither a backend test nor an explicit Frontend delegation in a spec.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ID = re.compile(r"\b(F-\d{2}[a-z]?|RF-\d{2}|RF-admin|SEC-[a-z]+|PT-AC-\d+|PT-\d+)\b")
DELEGATION = re.compile(r"\*\*Verificado no Frontend\*\*:\s*(.+)")

PLAN_IDS = [f"F-{n:02d}" for n in range(1, 51)] + ["F-03a", "F-15a", "F-15b", "F-22a", "F-22b"]


def _req_ids(decorator: ast.expr) -> list[str]:
    """`@pytest.mark.req("F-01")` → ["F-01"]."""
    if (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "req"
        and decorator.args
        and isinstance(decorator.args[0], ast.Constant)
        and isinstance(decorator.args[0].value, str)
    ):
        return [decorator.args[0].value]
    return []


def collect_tests(root: Path) -> dict[str, list[str]]:
    by_id: dict[str, list[str]] = defaultdict(list)
    for path in sorted((root / "tests").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                for decorator in node.decorator_list:
                    for req in _req_ids(decorator):
                        by_id[req].append(f"{rel}::{node.name}")
    return by_id


def collect_specs(root: Path) -> tuple[dict[str, list[str]], set[str]]:
    cited: dict[str, list[str]] = defaultdict(list)
    delegated: set[str] = set()
    for spec in sorted((root / "specs").glob("*/spec.md")):
        text = spec.read_text(encoding="utf-8")
        name = spec.parent.name
        for found in sorted(set(ID.findall(text))):
            cited[found].append(name)
        for m in DELEGATION.finditer(text):
            delegated.update(ID.findall(m.group(1)))
    return cited, delegated


def sort_key(req: str) -> tuple[str, int, str]:
    m = re.match(r"([A-Z-]+?)-?(\d+)?([a-z]*)$", req)
    if not m:
        return (req, 0, "")
    return (m.group(1), int(m.group(2) or 0), m.group(3))


def build(root: Path) -> tuple[str, list[str]]:
    tests = collect_tests(root)
    cited, delegated = collect_specs(root)
    lines = ["# Matriz de rastreabilidade (Backend)", "", "| Requisito | Specs | Testes |", "|---|---|---|"]
    for req in sorted(set(tests) | set(cited) | set(PLAN_IDS), key=sort_key):
        test_cell = "<br>".join(f"`{t}`" for t in tests.get(req, []))
        if not test_cell and req in delegated:
            test_cell = "_Frontend_"
        lines.append(f"| {req} | {', '.join(cited.get(req, [])) or '—'} | {test_cell or '**sem teste**'} |")

    problems = []
    for req in PLAN_IDS:
        if req not in tests and req not in delegated:
            problems.append(f"{req}: no backend test and no Frontend delegation in specs")
    untested = sorted((set(cited) - set(tests)) - delegated, key=sort_key)
    lines += ["", "## Ids citados nas specs sem teste no backend", ""]
    lines += [f"- {req} ({', '.join(cited[req])})" for req in untested] or ["- nenhum"]
    lines += [
        "",
        "## Delegados ao Frontend",
        "",
        f"- {', '.join(sorted(delegated, key=sort_key)) or 'nenhum'}",
    ]
    covered = sum(1 for r in PLAN_IDS if r in tests)
    lines += [
        "",
        f"Cenários F do plano: {covered}/{len(PLAN_IDS)} com teste no backend, "
        f"{sum(1 for r in PLAN_IDS if r not in tests and r in delegated)} delegados ao Frontend.",
    ]
    return "\n".join(lines) + "\n", problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report, problems = build(ROOT)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
    print(report)
    if problems:
        print("TRACEABILITY GAPS:\n  " + "\n  ".join(problems))
        sys.exit(1)


if __name__ == "__main__":
    main()
