"""Step C (spec 010): product groups, package sizes and price comparisons over the user's own receipts.

A line's group and size are worked out when they are read, from the confirmed description (D7), the
catalogue and the user's own rules: nothing is stored on the receipt. So a new catalogue version (D14) or
a rule from «Por completar» (D13) applies at once to every past purchase, and removing the rule undoes
it. Spending totals never depend on any of this; only comparisons do.

Comparisons use only the user's confirmed purchases of the same group, in the same comparison base, with
a known size (D9–D12). Money stays exact: Decimal cents, rounded half up only in the response.
"""

from __future__ import annotations

import heapq
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from basketiq.app_config import AppConfig
from basketiq.db.models import Receipt, ReceiptLine, UserLineRule
from basketiq.domain.grouping import Catalog, Classification, GroupStatus, classify
from basketiq.domain.money import round_cents, to_base_unit
from basketiq.domain.normalization import normalize_key
from basketiq.domain.size import Size, extract_size, is_plausible, plain
from basketiq.domain.status import ReceiptStatus, Unit
from basketiq.services import analytics

WEIGHED = (Unit.KG, Unit.G, Unit.L, Unit.ML)
SUGGESTIONS = 5

RuleKey = tuple[str, str]  # (chain, normalised description); "" for a receipt without a chain


def rule_key(chain: str | None, description: str) -> RuleKey:
    """How a user's rule is found: the stored chain is never NULL, so the unique constraint holds."""
    return chain or "", normalize_key(description)


class RuleError(ValueError):
    """A rule the catalogue cannot accept (unknown group, implausible size, nothing to change)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LineGroup:
    """What step C knows about one line: its group, its size and its quantity in the group's base."""

    status: GroupStatus
    group: str | None
    source: str | None  # "catalog" or "user"
    hint: str | None  # node to suggest from, when «por agrupar»
    candidates: tuple[str, ...]
    size: Size | None
    size_source: str | None  # "description", "user" or "receipt" (the weight of a weighed line)
    basis: str | None  # the group's first base the line can be measured in (D1); implies a group
    quantity: Decimal | None  # in `basis`


def resolve(
    classification: Classification,
    described: Size | None,
    quantity: Decimal | None,
    unit: str | None,
    rule: UserLineRule | None,
    catalog: Catalog,
) -> LineGroup:
    """One line: its catalogue classification and printed size, overridden by the user's rule.

    The status, the group and the basis depend only on whether there is a (positive) quantity, never on its
    amount; the amount only scales the returned quantity. «Por completar» relies on this.
    """
    status, group = classification.status, classification.group
    source = "catalog" if group else None
    if rule is not None and rule.not_comparable:
        status, group, source = GroupStatus.NOT_COMPARABLE, None, "user"
    elif rule is not None and rule.group_slug and _is_group(catalog, rule.group_slug):
        node = catalog.nodes[rule.group_slug]
        status = GroupStatus.GROUPED if node.comparable else GroupStatus.NOT_COMPARABLE
        group, source = node.slug, "user"

    size: Size | None = None
    size_source: str | None = None
    measure: Decimal | None = None
    if unit in WEIGHED:
        if quantity:  # the weight printed on the receipt is the size (origin «talão» in the CSV)
            measure, base = to_base_unit(quantity, unit)
            size, size_source = Size(plain(measure), base), "receipt"
    else:
        if rule is not None and rule.size_value is not None and rule.size_unit:
            size, size_source = Size(plain(rule.size_value), rule.size_unit), "user"
        elif described is not None:
            size, size_source = described, "description"
        if size is not None and quantity:
            measure = quantity * size.value

    basis = None
    if status == GroupStatus.GROUPED and group and size and measure and measure > 0:
        basis = next((b for b in catalog.nodes[group].bases if b == size.unit), None)
    return LineGroup(
        status,
        group,
        source,
        classification.hint,
        classification.candidates,
        size,
        size_source,
        basis,
        measure if basis else None,
    )


def _is_group(catalog: Catalog, slug: str) -> bool:
    node = catalog.nodes.get(slug)
    return node is not None and node.is_group


class _Resolver:
    """Resolves many lines of one user: rules loaded once, each spelling classified and sized once."""

    def __init__(self, db: Session, user_id: uuid.UUID, catalog: Catalog) -> None:
        self.catalog = catalog
        rows = db.scalars(select(UserLineRule).where(UserLineRule.user_id == user_id))
        self.rules: dict[RuleKey, UserLineRule] = {(r.chain, r.description_key): r for r in rows}
        self._seen: dict[tuple[str | None, str], tuple[RuleKey, Classification, Size | None]] = {}

    def key_of(self, raw: str, merchant: str | None) -> RuleKey:
        return self._read(raw, merchant)[0]

    def _read(self, raw: str, merchant: str | None) -> tuple[RuleKey, Classification, Size | None]:
        found = self._seen.get((merchant, raw))
        if found is None:
            found = (
                rule_key(merchant, raw),
                classify(raw, merchant, self.catalog),
                extract_size(raw, self.catalog.plausible_sizes),
            )
            self._seen[(merchant, raw)] = found
        return found

    def __call__(
        self, raw: str, merchant: str | None, quantity: Decimal | None, unit: str | None
    ) -> LineGroup:
        key, classification, described = self._read(raw, merchant)
        return resolve(classification, described, quantity, unit, self.rules.get(key), self.catalog)


def _confirmed(stmt: Any, user_id: uuid.UUID) -> Any:
    """Restrict a select over lines to the user's confirmed purchases (the lines spending counts)."""
    return (
        stmt.select_from(ReceiptLine)
        .join(Receipt, Receipt.id == ReceiptLine.receipt_id)
        .where(
            Receipt.user_id == user_id,
            Receipt.processing_status == ReceiptStatus.CONFIRMED,
            Receipt.purchase_at.is_not(None),
            ReceiptLine.line_total_cents.is_not(None),
        )
    )


def _confirmed_lines(
    db: Session, user_id: uuid.UUID, start: datetime | None = None, end: datetime | None = None
) -> list[Any]:
    stmt = _confirmed(
        select(
            Receipt.id.label("receipt_id"),
            Receipt.merchant,
            Receipt.merchant_name,
            ReceiptLine.raw_description,
            ReceiptLine.quantity_decimal.label("quantity"),
            ReceiptLine.unit,
            analytics._net.label("net"),
        ),
        user_id,
    ).order_by(Receipt.purchase_at, ReceiptLine.line_number)  # the latest spelling of a description wins
    if start is not None:
        stmt = stmt.where(Receipt.purchase_at >= start)
    if end is not None:
        stmt = stmt.where(Receipt.purchase_at < end)
    return list(db.execute(stmt).all())


def group_ref(catalog: Catalog, slug: str | None) -> dict[str, str] | None:
    """A group for the response; None for no group or a slug a newer catalogue no longer has (D14)."""
    if slug is None or not _is_group(catalog, slug):
        return None
    node = catalog.nodes[slug]
    return {"slug": slug, "name": node.name, "class_slug": node.class_slug}


def _size_out(size: Size | None, source: str | None) -> dict[str, Any] | None:
    return {"value": str(size.value), "unit": size.unit, "source": source} if size else None


@dataclass
class _Totals:
    net: int = 0
    quantity: Decimal = Decimal(0)
    receipts: set[uuid.UUID] = field(default_factory=set)

    def add(self, net: int, quantity: Decimal, receipt_id: uuid.UUID) -> None:
        self.net += net
        self.quantity += quantity
        self.receipts.add(receipt_id)

    @property
    def purchases(self) -> int:
        return len(self.receipts)

    @property
    def average(self) -> Decimal:
        return Decimal(self.net) / self.quantity


# ---------------------------------------------------------- D11: one receipt's lines against the average


def receipt_lines_check(
    db: Session, receipt: Receipt, catalog: Catalog, config: AppConfig
) -> tuple[dict[uuid.UUID, dict[str, Any]], int]:
    """Per line of a confirmed receipt: its group and «above / below what you usually pay» (D11); and how
    many of its descriptions are left «por agrupar» (the sentence after confirming, D13). Empty before
    confirmation."""
    if receipt.processing_status != ReceiptStatus.CONFIRMED or receipt.purchase_at is None:
        return {}, 0
    resolver = _Resolver(db, receipt.user_id, catalog)
    window = timedelta(days=config.comparison_window_days)
    earlier: dict[tuple[str, str], _Totals] = defaultdict(_Totals)
    # Strictly earlier purchases: the compared receipt itself is never in its own average.
    for row in _confirmed_lines(db, receipt.user_id, receipt.purchase_at - window, receipt.purchase_at):
        lg = resolver(row.raw_description, row.merchant, row.quantity, row.unit)
        if lg.basis and lg.group and lg.quantity:
            earlier[(lg.group, lg.basis)].add(row.net, lg.quantity, row.receipt_id)

    out: dict[uuid.UUID, dict[str, Any]] = {}
    ungrouped: set[RuleKey] = set()
    for line in receipt.lines:
        if line.line_total_cents is None:
            continue
        lg = resolver(line.raw_description, receipt.merchant, line.quantity_decimal, line.unit)
        if lg.status == GroupStatus.UNGROUPED:
            ungrouped.add(resolver.key_of(line.raw_description, receipt.merchant))
        net = line.line_total_cents - (line.discount_cents or 0)
        out[line.id] = {
            "status": lg.status.value,
            "group": group_ref(catalog, lg.group),
            "group_source": lg.source,
            "size": _size_out(lg.size, lg.size_source),
            "price_check": _price_check(lg, net, earlier, config)
            if lg.status == GroupStatus.GROUPED
            else None,
        }
    return out, len(ungrouped)


def _price_check(
    lg: LineGroup, net: int, earlier: dict[tuple[str, str], _Totals], config: AppConfig
) -> dict[str, Any]:
    base = {"window_days": config.comparison_window_days, "needed": config.comparison_min_purchases}
    if not (lg.basis and lg.group and lg.quantity):
        return {**base, "status": "missing_size", "purchases": 0, "basis": None}
    totals = earlier.get((lg.group, lg.basis))
    purchases = totals.purchases if totals else 0
    price = Decimal(net) / lg.quantity
    result = {**base, "basis": lg.basis, "purchases": purchases, "price_cents": round_cents(price)}
    if totals is None or purchases < config.comparison_min_purchases:
        return {**result, "status": "no_average"}
    average = totals.average
    difference = price - average
    effect = difference * lg.quantity
    clear = abs(difference) * 100 >= config.comparison_difference_pct * average and abs(effect) >= (
        config.comparison_min_effect_cents
    )
    status = ("above" if difference > 0 else "below") if clear else "normal"
    return {
        **result,
        "status": status,
        "average_cents": round_cents(average),
        "effect_cents": round_cents(effect),
    }


# ------------------------------------------------------------------ D12: one group between chains


def group_comparison(
    db: Session, user_id: uuid.UUID, slug: str, catalog: Catalog, config: AppConfig, today: date, tz_name: str
) -> dict[str, Any] | None:
    """What the user paid for one group in each chain, in the window (D12). None for an unknown group."""
    if not _is_group(catalog, slug):
        return None
    window = analytics.Filters(today - timedelta(days=config.comparison_window_days - 1), today)
    start, end = analytics._bounds(window, ZoneInfo(tz_name))
    resolver = _Resolver(db, user_id, catalog)

    by_basis: dict[str, dict[tuple[str | None, str], _Totals]] = defaultdict(lambda: defaultdict(_Totals))
    for row in _confirmed_lines(db, user_id, start, end):
        lg = resolver(row.raw_description, row.merchant, row.quantity, row.unit)
        if lg.group == slug and lg.basis and lg.quantity:
            store = analytics.merchant_label(row.merchant, row.merchant_name)
            by_basis[lg.basis][store].add(row.net, lg.quantity, row.receipt_id)
    node = catalog.nodes[slug]
    basis = next((b for b in node.bases if b in by_basis), node.bases[0] if node.bases else None)
    totals = by_basis.get(basis, {}) if basis else {}

    overall = _Totals()
    for t in totals.values():
        overall.net += t.net
        overall.quantity += t.quantity
        overall.receipts |= t.receipts
    chains = [
        {
            "merchant": merchant,
            "merchant_name": store_name,
            "purchases": t.purchases,
            "average_cents": round_cents(t.average),
            "missing_purchases": max(0, config.comparison_min_purchases - t.purchases),
        }
        # by name, never by price (no ranking, D12)
        for (merchant, store_name), t in sorted(totals.items(), key=lambda item: item[0][1])
    ]
    result: dict[str, Any] = {
        "group": group_ref(catalog, slug),
        "basis": basis,
        "window_days": config.comparison_window_days,
        "date_from": window.date_from.isoformat(),
        "date_to": today.isoformat(),
        "purchases": overall.purchases,
        "average_cents": round_cents(overall.average) if totals else None,
        "chains": chains,
        "needed": config.comparison_min_purchases,
        "typical_difference_cents": None,
    }
    if not totals:
        return {**result, "status": "no_purchases"}
    if len(totals) == 1:
        return {**result, "status": "single_chain"}
    enough = [t for t in totals.values() if t.purchases >= config.comparison_min_purchases]
    if len(enough) < config.comparison_min_chains:
        return {**result, "status": "not_enough_purchases"}
    high = max(enough, key=lambda t: t.average)
    low = min(enough, key=lambda t: t.average)
    difference = high.average - low.average
    typical = (high.quantity + low.quantity) / (high.purchases + low.purchases)  # D12(b): two chains together
    effect = difference * typical
    clear = difference * 100 >= config.comparison_difference_pct * overall.average and effect >= (
        config.comparison_min_effect_cents
    )
    if not clear:
        return {**result, "status": "no_clear_difference"}
    return {**result, "status": "clear_difference", "typical_difference_cents": round_cents(effect)}


# ------------------------------------------------------------------ D13: «Por completar» and the user's rules


def to_complete(
    db: Session, user_id: uuid.UUID, tab: str, catalog: Catalog, page_size: int
) -> dict[str, Any]:
    """Descriptions «por agrupar» (tab ``ungrouped``) or grouped without a size (``missing_size``), the
    most spending first, ``page_size`` at a time; the rest can wait (D13)."""
    resolver = _Resolver(db, user_id, catalog)
    # One row per spelling, unit and «has a quantity» (all a line's group and size depend on), summed in SQL.
    has_quantity = func.coalesce(ReceiptLine.quantity_decimal, 0) > 0
    keys = (Receipt.merchant, ReceiptLine.raw_description, ReceiptLine.unit, has_quantity)
    spellings = db.execute(
        _confirmed(
            select(
                *keys[:-1],
                has_quantity.label("has_quantity"),
                func.max(Receipt.merchant_name).label("merchant_name"),
                func.count().label("lines"),
                func.array_agg(func.distinct(Receipt.id)).label("receipts"),
                func.sum(analytics._net).label("net"),
            ),
            user_id,
        )
        .group_by(*keys)
        .order_by(func.max(Receipt.purchase_at))  # the latest confirmed spelling comes last and wins
    ).all()
    items: dict[RuleKey, dict[str, Any]] = {}
    for row in spellings:
        quantity = Decimal(1) if row.has_quantity else None  # only its presence matters (see resolve)
        lg = resolver(row.raw_description, row.merchant, quantity, row.unit)
        if tab == "ungrouped":
            wanted = lg.status == GroupStatus.UNGROUPED
        else:
            wanted = lg.status == GroupStatus.GROUPED and lg.basis is None
        if not wanted:
            continue
        key = resolver.key_of(row.raw_description, row.merchant)
        rule = resolver.rules.get(key)
        item = items.setdefault(
            key,
            {
                "chain": row.merchant,
                "merchant_name": analytics.merchant_label(row.merchant, row.merchant_name)[1],
                "description_key": key[1],
                "unit": row.unit,
                "lines": 0,
                "receipts": set(),
                "spent_cents": 0,
                "group": group_ref(catalog, lg.group),
                "hint": _node_ref(catalog, lg.hint),
                "suggestions": [group_ref(catalog, s) for s in lg.candidates[:SUGGESTIONS]],
                "rule_id": rule.id if rule else None,
            },
        )
        item["description"] = row.raw_description
        item["lines"] += row.lines
        item["receipts"].update(row.receipts)
        item["spent_cents"] += int(row.net)
    page = heapq.nsmallest(page_size, items.values(), key=lambda i: (-i["spent_cents"], i["description_key"]))
    for i in page:
        i["purchases"] = len(i.pop("receipts"))
    return {"tab": tab, "total": len(items), "page_size": page_size, "items": page}


def _node_ref(catalog: Catalog, slug: str | None) -> dict[str, str] | None:
    return {"slug": slug, "name": catalog.nodes[slug].name} if slug else None


def _rule_out(rule: UserLineRule, catalog: Catalog, applied: int, description: str | None) -> dict[str, Any]:
    size = (
        Size(plain(rule.size_value), rule.size_unit)
        if rule.size_value is not None and rule.size_unit
        else None
    )
    return {
        "id": rule.id,
        "chain": rule.chain or None,
        "description_key": rule.description_key,
        "description": description,
        "group": group_ref(catalog, rule.group_slug),
        "not_comparable": rule.not_comparable,
        "size": _size_out(size, "user"),
        "applied_lines": applied,
        "updated_at": rule.updated_at,
    }


def _lines_by_key(db: Session, user_id: uuid.UUID) -> dict[RuleKey, tuple[int, str]]:
    """(chain, description key) → (confirmed lines, latest spelling) of the user, counted in the database."""
    rows = db.execute(
        _confirmed(select(Receipt.merchant, ReceiptLine.raw_description, func.count()), user_id)
        .group_by(Receipt.merchant, ReceiptLine.raw_description)
        .order_by(func.max(Receipt.purchase_at))  # the latest spelling comes last and wins
    ).all()
    out: dict[RuleKey, tuple[int, str]] = {}
    for merchant, raw, count in rows:
        key = rule_key(merchant, raw)
        out[key] = (out.get(key, (0, raw))[0] + count, raw)
    return out


def list_rules(db: Session, user_id: uuid.UUID, catalog: Catalog) -> list[dict[str, Any]]:
    """«Atribuídos por si»: the user's own decisions, newest first."""
    rules = list(
        db.scalars(
            select(UserLineRule)
            .where(UserLineRule.user_id == user_id)
            .order_by(UserLineRule.updated_at.desc())
        )
    )
    lines = _lines_by_key(db, user_id) if rules else {}
    return [_rule_out(r, catalog, *lines.get((r.chain, r.description_key), (0, None))) for r in rules]


def apply_rules(
    db: Session,
    user_id: uuid.UUID,
    targets: list[tuple[str | None, str]],
    changes: dict[str, Any],
    catalog: Catalog,
) -> dict[str, Any]:
    """Set the given fields of the rule of each (chain, description); a field left out keeps its value,
    and a rule left with nothing in it is removed. ``changes`` may hold ``group`` (slug or None),
    ``not_comparable`` (bool or None) and ``size`` ({value, unit} or None). Applies to every confirmed line
    of those descriptions, past and future (D13)."""
    if not changes:
        raise RuleError("nothing_to_change", "Indique o grupo, o tamanho ou «não comparável».")
    group = changes.get("group")
    if group is not None and not _is_group(catalog, group):
        raise RuleError("unknown_group", "Esse grupo não existe.")
    size = Size(plain(changes["size"]["value"]), changes["size"]["unit"]) if changes.get("size") else None
    if size is not None and not is_plausible(size, catalog.plausible_sizes):
        raise RuleError("implausible_size", "Esse tamanho não é plausível.")

    keys = list(dict.fromkeys(rule_key(chain, description) for chain, description in targets))
    existing = {
        (r.chain, r.description_key): r
        for r in db.scalars(
            select(UserLineRule).where(
                UserLineRule.user_id == user_id,
                UserLineRule.description_key.in_([k[1] for k in keys]),
            )
        )
    }
    kept: list[tuple[RuleKey, UserLineRule]] = []
    for key in keys:
        rule = existing.get(key) or UserLineRule(user_id=user_id, chain=key[0], description_key=key[1])
        if "group" in changes:
            rule.group_slug = group
            if group is not None:
                rule.not_comparable = False
        if "not_comparable" in changes:
            rule.not_comparable = bool(changes["not_comparable"])
            if rule.not_comparable:
                rule.group_slug = None
        if "size" in changes:
            rule.size_value, rule.size_unit = (size.value, size.unit) if size else (None, None)
        if rule.group_slug is None and not rule.not_comparable and rule.size_value is None:
            if rule in db:
                db.delete(rule)
            continue
        db.add(rule)
        kept.append((key, rule))
    db.flush()
    lines = _lines_by_key(db, user_id)
    result = [_rule_out(rule, catalog, *lines.get(key, (0, None))) for key, rule in kept]
    db.commit()
    return {"rules": result, "applied_lines": sum(lines.get(k, (0, ""))[0] for k in keys)}


def delete_rule(db: Session, user_id: uuid.UUID, rule_id: uuid.UUID) -> bool:
    rule = db.get(UserLineRule, rule_id)
    if rule is None or rule.user_id != user_id:
        return False
    db.delete(rule)
    db.commit()
    return True


def catalog_tree(catalog: Catalog) -> dict[str, Any]:
    """Classes and their groups, for the person to pick one (D13: «Outro grupo»)."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in catalog.groups:
        groups[g.class_slug].append(
            {"slug": g.slug, "name": g.name, "bases": list(g.bases), "comparable": g.comparable}
        )
    classes = [
        {"slug": n.slug, "name": n.name, "groups": groups[n.slug]}
        for n in catalog.nodes.values()
        if n.parent is None
    ]
    return {"version": catalog.version, "classes": classes}


def product_group(db: Session, user_id: uuid.UUID, product_id: uuid.UUID, catalog: Catalog) -> str | None:
    """The group of the user's latest confirmed line of a product, for the product screen (D12)."""
    row = db.execute(
        _confirmed(
            select(
                Receipt.merchant, ReceiptLine.raw_description, ReceiptLine.quantity_decimal, ReceiptLine.unit
            ),
            user_id,
        )
        .where(ReceiptLine.normalized_product_id == product_id)
        .order_by(Receipt.purchase_at.desc(), ReceiptLine.line_number.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    merchant, raw, qty, unit = row
    chain, key = rule_key(merchant, raw)
    rule = db.scalar(
        select(UserLineRule).where(
            UserLineRule.user_id == user_id, UserLineRule.chain == chain, UserLineRule.description_key == key
        )
    )
    lg = resolve(classify(raw, merchant, catalog), None, qty, unit, rule, catalog)
    return lg.group if lg.status == GroupStatus.GROUPED else None
