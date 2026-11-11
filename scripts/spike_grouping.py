"""Step C data spike (spec 015 US0, spec 010): how much of a set of receipts the catalogue groups and sizes.

Reads ``*.expected.json`` files below a folder (default ``../Receipts``, outside git) and prints **aggregates
only**: counts per chain, never a description, a price or a date, so the output can be published. The folder
``_blind`` is skipped and never opened.

    python scripts/spike_grouping.py [folder]
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from basketiq.catalog import get_catalog
from basketiq.domain.grouping import GroupStatus, classify
from basketiq.domain.size import extract_size
from basketiq.services.groups import resolve

KEYS = [
    "linhas",
    "grouped",
    "not_comparable",
    "ungrouped",
    "conflito",
    "com_pista",
    "com_tamanho",
    "agrupada_com_tamanho",
]


def main(folder: Path) -> None:
    catalog = get_catalog()
    per_chain: dict[str, Counter[str]] = defaultdict(Counter)
    for path in sorted(folder.rglob("*.expected.json")):
        if "_blind" in path.parts:
            continue
        receipt = json.loads(path.read_text(encoding="utf-8"))
        chain = receipt.get("merchant")
        for line in receipt.get("lines", []):
            description = line.get("raw_description") or line.get("description") or ""
            quantity = Decimal(str(line["quantity"])) if line.get("quantity") is not None else None
            unit = (line.get("unit") or "").lower() or None
            # The same resolution the API uses, without user rules.
            lg = resolve(
                classify(description, chain, catalog),
                extract_size(description, catalog.plausible_sizes),
                quantity,
                unit,
                None,
                catalog,
            )
            c = per_chain[chain or "OUTRA"]
            c["linhas"] += 1
            c[lg.status.value] += 1
            if lg.status == GroupStatus.UNGROUPED:
                if lg.hint:
                    c["com_pista"] += 1
                elif len(lg.candidates) > 1:
                    c["conflito"] += 1
            if lg.status != GroupStatus.NOT_COMPARABLE and lg.size is not None:
                c["com_tamanho"] += 1
            if lg.basis is not None:
                c["agrupada_com_tamanho"] += 1
    print("cadeia;" + ";".join(KEYS))
    total: Counter[str] = Counter()
    for chain, c in sorted(per_chain.items()):
        total.update(c)
        print(chain + ";" + ";".join(str(c[k]) for k in KEYS))
    print("TOTAL;" + ";".join(str(total[k]) for k in KEYS))
    print(f"catalogue version {catalog.version}, {len(catalog.groups)} groups")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../Receipts"))
