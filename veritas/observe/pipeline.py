"""Orchestration — the three stages wired into baseline / diff / check / accept operations.

    capture (existing) -> project (facts) -> cull (variance) -> label (meaning) -> ledger
                                                                                      |
                                          new run A --> project --> diff/check <-------+

This is the application layer the Workspace and CLI call; it holds no policy beyond sequencing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import Observation
from .facts import project_all, Fact
from .variance import cull, VarianceReport
from .label import Labeler, StubLabeler, Label
from .ledger import Ledger, LedgerStore, build_ledger
from .diff import diff, check, CheckReport, DeltaItem


@dataclass
class BaselineResult:
    ledger: Ledger
    variance: VarianceReport


def build_baseline(observations: list[Observation], env: str, version: int = 1,
                   labeler: Optional[Labeler] = None, note: str = "") -> BaselineResult:
    """Stages 1-3 + ledger assembly from a set of captured runs (ideally repeats of the same
    scenarios, so variance-culling has something to cull)."""
    labeler = labeler or StubLabeler()
    facts = [f for f in project_all(observations) if env is None or f.env == env]
    report = cull(facts)
    observation_facts = [cf.fact for cf in report.observations()]  # stable + unconfirmed
    labels: dict[str, Label] = labeler.label(observation_facts)
    ledger = build_ledger(report.culled, labels, env=env, version=version, note=note)
    return BaselineResult(ledger=ledger, variance=report)


def run_facts(observations: list[Observation], env: str) -> list[Fact]:
    """Project a new run (A) to facts for diffing. (No culling: A is the candidate under test;
    the trusted/stable set lives in the ledger.)"""
    return [f for f in project_all(observations) if env is None or f.env == env]


def check_run(ledger: Ledger, observations: list[Observation], env: str,
              delta: Optional[list[DeltaItem]] = None) -> CheckReport:
    return check(ledger, run_facts(observations, env), delta or [])


def accept_run(store: LedgerStore, prev: Ledger, observations: list[Observation], env: str,
               labeler: Optional[Labeler] = None, note: str = "") -> Ledger:
    """Advance the baseline: L0 <- A. Re-derives the ledger from the accepted run and bumps the
    version. Carries forward existing labels so accepted facts keep their frozen meaning (label
    once; only genuinely new anchors get freshly labeled)."""
    carry = _CarryForwardLabeler(prev, base=labeler or StubLabeler())
    res = build_baseline(observations, env=env, version=prev.version + 1, labeler=carry, note=note)
    store.save(res.ledger)
    return res.ledger


class _CarryForwardLabeler:
    """Reuse a prior ledger's labels for anchors it already knew; defer to `base` for new ones.
    Keeps the meaning layer stable across accepts (no needless re-labeling, no label churn)."""

    def __init__(self, prev: Ledger, base: Labeler):
        self._known = {e.anchor: e.label for e in prev.entries}
        self._base = base

    def label(self, facts: list[Fact]) -> dict[str, Label]:
        unknown = [f for f in facts if f.anchor not in self._known]
        fresh = self._base.label(unknown) if unknown else {}
        return {f.anchor: self._known.get(f.anchor) or fresh.get(f.anchor) for f in facts}
