"""The mechanical diff and the PRD-delta check — the payoff of the whole design.

A PRD is a *delta on observations*: "for this boundary, the value now becomes Y" plus the implicit
"everything else stays as it was." Verification is then purely mechanical:

    changes = diff(ledger L0, new run A)            # value-equality over WATCHED stable facts only
    report  = check(changes, declared_delta D)
        intended    = changes that D asked for
        regressions = WATCHED changes D did NOT ask for   <-- the free catch, no dependency graph
        missing     = D items that did not actually happen

The dark-coupling regression falls out for free: we never mapped an edge, we just watched the
observation the edge would perturb. No LLM renders this verdict — equality does. The diff compares
only `watched` ledger entries, so noise and low-salience facts can never raise a false alarm.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from .facts import Fact, FactKind, canonical
from .label import group_of, Salience
from .ledger import Ledger, LedgerEntry


# --------------------------------------------------------------------------- declared delta (the PRD)
@dataclass
class DeltaItem:
    """One declared observation change in a PRD. Address a single `anchor` or a whole `group`
    (key-family). `expect`: 'changed' (value moved at all) | 'value' (moved to exactly `value`)
    | 'new' (a new observation appears) | 'gone' (an observation should disappear)."""
    anchor: Optional[str] = None
    group: Optional[str] = None
    expect: str = "changed"
    value: Any = None
    note: str = ""

    def matches_anchor(self, anchor: str) -> bool:
        if self.anchor is not None:
            return self.anchor == anchor
        if self.group is not None:
            return group_of(anchor) == self.group
        return False

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}

    @staticmethod
    def from_dict(d: dict) -> "DeltaItem":
        return DeltaItem(anchor=d.get("anchor"), group=d.get("group"),
                         expect=d.get("expect", "changed"), value=d.get("value"),
                         note=d.get("note", ""))


# --------------------------------------------------------------------------- observed change
@dataclass
class FactChange:
    anchor: str
    condition: str
    change: str                 # "changed" | "new" | "gone"
    before: Any = None
    after: Any = None
    salience: str = "medium"
    name: str = ""
    coverage_gap: bool = False  # for "gone": the path wasn't exercised in A (info, not regression)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CheckReport:
    verdict: str                     # "PASS" | "REGRESSION" | "INCOMPLETE"
    intended: list[FactChange] = field(default_factory=list)
    regressions: list[FactChange] = field(default_factory=list)
    missing: list[DeltaItem] = field(default_factory=list)
    coverage_gaps: list[FactChange] = field(default_factory=list)
    unchanged_watched: int = 0

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "intended": [c.to_dict() for c in self.intended],
            "regressions": [c.to_dict() for c in self.regressions],
            "missing": [d.to_dict() for d in self.missing],
            "coverage_gaps": [c.to_dict() for c in self.coverage_gaps],
            "unchanged_watched": self.unchanged_watched,
        }


# --------------------------------------------------------------------------- the diff
def _executed_methods(facts_A: list[Fact]) -> set[str]:
    return {f.method for f in facts_A if f.kind == FactKind.PATH and f.method}


def diff(ledger: Ledger, facts_A: list[Fact]) -> list[FactChange]:
    """Diff a new run's facts against the watched baseline. Equality under canonicalization;
    only `watched` ledger entries participate (whitelist => no false alarms from noise/low)."""
    watched: dict[tuple[str, str], LedgerEntry] = {e.key(): e for e in ledger.watched()}
    known: set[tuple[str, str]] = {e.key() for e in ledger.entries}  # full set: known != watched
    a_index: dict[tuple[str, str], Fact] = {f.key(): f for f in facts_A}
    executed = _executed_methods(facts_A)
    changes: list[FactChange] = []

    for key, entry in watched.items():
        af = a_index.get(key)
        if af is None:
            # absent in A: regression only if the path ran but the value vanished; otherwise
            # it's a coverage gap (the scenario simply didn't exercise this condition).
            gap = bool(entry.fact.method) and entry.fact.method not in executed
            changes.append(FactChange(anchor=entry.anchor, condition=key[1], change="gone",
                                      before=entry.fact.value, after=None,
                                      salience=entry.label.salience.value, name=entry.label.name,
                                      coverage_gap=gap))
        elif canonical(af.value) != canonical(entry.fact.value):
            changes.append(FactChange(anchor=entry.anchor, condition=key[1], change="changed",
                                      before=entry.fact.value, after=af.value,
                                      salience=entry.label.salience.value, name=entry.label.name))

    # NEW: facts present in A whose (anchor, condition) the ledger has NEVER recorded — not
    # merely "not watched" (a known-but-ignored low-salience fact must not resurface as new).
    for key, af in a_index.items():
        if key not in known and af.kind in (FactKind.VALUE, FactKind.CONFIG, FactKind.STATE):
            changes.append(FactChange(anchor=af.anchor, condition=key[1], change="new",
                                      before=None, after=af.value))
    return changes


def unchanged_count(ledger: Ledger, facts_A: list[Fact]) -> int:
    watched = {e.key(): e for e in ledger.watched()}
    a_index = {f.key(): f for f in facts_A}
    n = 0
    for key, entry in watched.items():
        af = a_index.get(key)
        if af is not None and canonical(af.value) == canonical(entry.fact.value):
            n += 1
    return n


# --------------------------------------------------------------------------- the check (PRD verify)
def _item_satisfied_by(item: DeltaItem, c: FactChange) -> bool:
    if not item.matches_anchor(c.anchor):
        return False
    if item.expect == "value":
        return c.change in ("changed", "new") and canonical(c.after) == canonical(item.value)
    if item.expect == "new":
        return c.change == "new"
    if item.expect == "gone":
        return c.change == "gone"
    return c.change in ("changed", "new")  # "changed" (default): any movement counts


def check(ledger: Ledger, facts_A: list[Fact], delta: list[DeltaItem]) -> CheckReport:
    """Verify a run against the baseline given the PRD's declared observation delta.

    PASS iff every declared change happened and NOTHING else among watched facts moved."""
    changes = diff(ledger, facts_A)
    report = CheckReport(verdict="PASS", unchanged_watched=unchanged_count(ledger, facts_A))

    real_changes = [c for c in changes if not (c.change == "gone" and c.coverage_gap)]
    report.coverage_gaps = [c for c in changes if c.change == "gone" and c.coverage_gap]

    matched_items: set[int] = set()
    for c in real_changes:
        hit = next((i for i, item in enumerate(delta) if _item_satisfied_by(item, c)), None)
        if hit is not None:
            matched_items.add(hit)
            report.intended.append(c)
        else:
            report.regressions.append(c)

    for i, item in enumerate(delta):
        if i not in matched_items:
            report.missing.append(item)

    if report.regressions:
        report.verdict = "REGRESSION"
    elif report.missing:
        report.verdict = "INCOMPLETE"
    return report
