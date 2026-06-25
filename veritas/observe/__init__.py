"""Observation-first layer for Veritas.

The existing engine is code-first: a code-anchored Expectation joined against captured values.
This layer inverts the source of truth — the *observation* is the unit of record, code is the
disposable transition function, and a PRD is a diff on the expected-observation ledger.

Three stages over the same captured traces:
  1. project   (facts.py)    — Observation -> field-level Facts (anchor, condition, value)
  2. cull      (variance.py) — mechanically separate stable behavior from noise via repetition
  3. label     (label.py)    — assign meaning only (name/salience/group); never values/verdicts

…persisted as a versioned ledger (ledger.py) and verified by a mechanical diff (diff.py): a new
run is PASS iff every declared observation-delta happened and nothing else among watched facts moved.
"""
from .facts import Fact, FactKind, project, project_all, canonical
from .variance import cull, VarianceReport, CulledFact, Stability
from .label import Label, Salience, Labeler, StubLabeler, ClaudeLabeler, group_of
from .ledger import Ledger, LedgerEntry, LedgerStore, build_ledger
from .diff import DeltaItem, FactChange, CheckReport, diff, check
from .coupling import Coupling, CouplingEdge, couplings, coupling_edges, blast_surface
from .pipeline import build_baseline, check_run, accept_run, run_facts, BaselineResult

__all__ = [
    "Fact", "FactKind", "project", "project_all", "canonical",
    "cull", "VarianceReport", "CulledFact", "Stability",
    "Label", "Salience", "Labeler", "StubLabeler", "ClaudeLabeler", "group_of",
    "Ledger", "LedgerEntry", "LedgerStore", "build_ledger",
    "DeltaItem", "FactChange", "CheckReport", "diff", "check",
    "Coupling", "CouplingEdge", "couplings", "coupling_edges", "blast_surface",
    "build_baseline", "check_run", "accept_run", "run_facts", "BaselineResult",
]
