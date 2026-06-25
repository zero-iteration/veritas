"""The ledger — the versioned baseline observation set `L0`, the system's source of truth.

"This is how the system currently behaves," made into a first-class, durable, versioned object.
A PRD is an edit to this ledger; verification is the new run diffed against it. On acceptance the
ledger advances (L0 <- A), so it is self-maintaining and accretes — every accepted change becomes
the next baseline, and every incident adds an observation that should have been watched.

Persistence (under .veritas/ledger/):
  * head.json          — the current ledger (version N)
  * history/v{n}.json  — immutable snapshot written on every accept

A ledger entry is a stable Fact + its frozen Label + a watch status. Labels are stored, not
recomputed each run (label once; re-label only when the fact set changes).
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from .facts import Fact
from .label import Label, Salience
from .variance import CulledFact, Stability


@dataclass
class LedgerEntry:
    fact: Fact
    label: Label
    status: str = "watched"          # "watched" | "ignored" (noise/low) | "unconfirmed"
    confirmations: int = 0           # runs that agreed on the value (variance evidence)

    @property
    def anchor(self) -> str:
        return self.fact.anchor

    def key(self) -> tuple[str, str]:
        return self.fact.key()

    def to_dict(self) -> dict:
        return {"fact": self.fact.to_dict(), "label": self.label.to_dict(),
                "status": self.status, "confirmations": self.confirmations}

    @staticmethod
    def from_dict(d: dict) -> "LedgerEntry":
        return LedgerEntry(fact=Fact.from_dict(d["fact"]), label=Label.from_dict(d["label"]),
                           status=d.get("status", "watched"), confirmations=d.get("confirmations", 0))


@dataclass
class Ledger:
    version: int
    env: str
    entries: list[LedgerEntry] = field(default_factory=list)
    created_ts: float = field(default_factory=time.time)
    note: str = ""

    def index(self) -> dict[tuple[str, str], LedgerEntry]:
        return {e.key(): e for e in self.entries}

    def watched(self) -> list[LedgerEntry]:
        return [e for e in self.entries if e.status == "watched"]

    def to_dict(self) -> dict:
        return {"version": self.version, "env": self.env, "created_ts": self.created_ts,
                "note": self.note, "entries": [e.to_dict() for e in self.entries]}

    @staticmethod
    def from_dict(d: dict) -> "Ledger":
        return Ledger(version=d["version"], env=d.get("env", "local"),
                      created_ts=d.get("created_ts", time.time()), note=d.get("note", ""),
                      entries=[LedgerEntry.from_dict(e) for e in d.get("entries", [])])

    def summary(self) -> dict:
        from collections import Counter
        st = Counter(e.status for e in self.entries)
        sal = Counter(e.label.salience.value for e in self.entries)
        return {"version": self.version, "env": self.env, "entries": len(self.entries),
                "status": dict(st), "salience": dict(sal)}


def build_ledger(culled: list[CulledFact], labels: dict[str, Label], env: str,
                 version: int = 1, note: str = "") -> Ledger:
    """Assemble a ledger from culled facts + their labels.

    Whitelist stance (a verifier that cries wolf is dead on arrival): only confirmed-deterministic,
    non-low-salience facts are `watched`. NOISE never enters. UNCONFIRMED and LOW-salience enter as
    not-watched so they are recorded and can be promoted later, but they do not raise regressions.
    """
    entries: list[LedgerEntry] = []
    for cf in culled:
        if cf.stability == Stability.NOISE:
            continue  # quotiented out — never compared, never alarms
        lbl = labels.get(cf.fact.anchor) or Label(name=cf.fact.anchor, salience=Salience.MEDIUM,
                                                   group=cf.fact.anchor)
        if cf.stability == Stability.STABLE and lbl.salience != Salience.LOW:
            status = "watched"
        elif cf.stability == Stability.UNCONFIRMED:
            status = "unconfirmed"
        else:
            status = "ignored"  # distributional (deterministic diff can't judge it) or low-salience
        entries.append(LedgerEntry(fact=cf.fact, label=lbl, status=status, confirmations=cf.runs))
    return Ledger(version=version, env=env, entries=entries, note=note)


class LedgerStore:
    def __init__(self, vdir: str):
        self.dir = os.path.join(vdir, "ledger")
        self.hist = os.path.join(self.dir, "history")
        os.makedirs(self.hist, exist_ok=True)
        self.head_path = os.path.join(self.dir, "head.json")

    def head(self) -> Optional[Ledger]:
        if not os.path.exists(self.head_path):
            return None
        return Ledger.from_dict(json.load(open(self.head_path)))

    def save(self, ledger: Ledger):
        """Write head + an immutable version snapshot (the ledger's audit trail)."""
        json.dump(ledger.to_dict(), open(self.head_path, "w"), indent=2)
        json.dump(ledger.to_dict(),
                  open(os.path.join(self.hist, f"v{ledger.version}.json"), "w"), indent=2)

    def next_version(self) -> int:
        h = self.head()
        return (h.version + 1) if h else 1
