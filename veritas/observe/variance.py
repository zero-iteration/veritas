"""Stage 2 — variance-culling: the firehose -> a clean observation set, mechanically.

Running the code gives EVERYTHING that executed, undifferentiated: real behavior and noise
(timestamps, ids, ordering) sit side by side. The cheapest faithful way to tell them apart is
not a model and not a guess about field names — it is *repetition*: run the same input more
than once and watch what moves.

  * value identical across all runs of the same (anchor, condition)        -> STABLE (deterministic)
  * value moves across identical input                                     -> NOISE (quotient out)
  * a FREQUENCY/numeric value that moves across identical input            -> DISTRIBUTIONAL
  * seen only once (n=1)                                                    -> UNCONFIRMED (can't yet tell)

This is the whole reason the later diff can be trusted: only STABLE facts enter the ledger, so a
later divergence is real behavior, never jitter. No LLM touches this stage.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .facts import Fact, FactKind, canonical


class Stability(str, Enum):
    STABLE = "stable"                  # confirmed deterministic (n>=2, all equal)
    UNCONFIRMED = "unconfirmed"        # seen once; deterministic-looking but not yet proven
    DISTRIBUTIONAL = "distributional"  # numeric value that varies across identical input
    NOISE = "noise"                    # non-numeric value that varies across identical input
    CAPTURE_GAP = "capture_gap"        # varies, but the CONDITION was truncated — may be false
                                       # non-determinism from a collapsed input (§11.1). LOUD, never
                                       # silently dropped: the worst failure for a verifier is to
                                       # mistake a capture gap for determinism and stop watching.


@dataclass
class CulledFact:
    """A fact plus the empirical verdict on whether it is a real, comparable observation."""
    fact: Fact
    stability: Stability
    runs: int                       # how many times this (anchor, condition) was observed
    distinct_values: int            # how many distinct canonical values were seen
    spread: list[Any] = field(default_factory=list)  # distinct values (for distributional/noise)

    @property
    def is_observation(self) -> bool:
        """Eligible to enter the ledger as a watchable deterministic observation."""
        return self.stability in (Stability.STABLE, Stability.UNCONFIRMED)


@dataclass
class VarianceReport:
    culled: list[CulledFact]

    def stable(self) -> list[CulledFact]:
        return [c for c in self.culled if c.stability == Stability.STABLE]

    def observations(self) -> list[CulledFact]:
        """STABLE + UNCONFIRMED — the deterministic-looking set (what a whitelist ledger draws from)."""
        return [c for c in self.culled if c.is_observation]

    def noise(self) -> list[CulledFact]:
        return [c for c in self.culled if c.stability == Stability.NOISE]

    def distributional(self) -> list[CulledFact]:
        return [c for c in self.culled if c.stability == Stability.DISTRIBUTIONAL]

    def capture_gaps(self) -> list[CulledFact]:
        """Facts the engine refuses to judge because capture truncated their condition. These are
        a CALL TO ACTION (raise a cap / add an unfold path), surfaced loudly — never swallowed."""
        return [c for c in self.culled if c.stability == Stability.CAPTURE_GAP]

    def summary(self) -> dict:
        from collections import Counter
        c = Counter(cf.stability.value for cf in self.culled)
        return {"total": len(self.culled), **c}


def _all_numeric(values: list[Any]) -> bool:
    def num(v):
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            return True
        if isinstance(v, str):
            try:
                float(v)
                return True
            except ValueError:
                return False
        return False
    return bool(values) and all(num(v) for v in values)


def cull(facts: list[Fact]) -> VarianceReport:
    """Group facts by (anchor, condition) and classify each group by how its value behaves
    across repeated observation. The input should be the projected facts of *multiple* runs of
    the same scenario(s); with a single run everything lands UNCONFIRMED (honestly so)."""
    groups: dict[tuple[str, str], list[Fact]] = defaultdict(list)
    for f in facts:
        groups[f.key()].append(f)

    culled: list[CulledFact] = []
    for key, fs in groups.items():
        runs = len(fs)
        by_canon: dict[str, Any] = {}
        for f in fs:
            by_canon.setdefault(f.canonical_value(), f.value)
        distinct = len(by_canon)
        rep = fs[-1]  # representative carries the latest value/sample
        spread = list(by_canon.values())
        # the condition is trustworthy only if EVERY contributing fact was fully captured; one
        # truncated arg poisons the whole (anchor, condition) group (§11.1).
        condition_complete = all(getattr(f, "condition_complete", True) for f in fs)

        if distinct == 1:
            if not condition_complete:
                stability = Stability.UNCONFIRMED  # value agrees, but on a PARTIAL condition — don't trust it
            else:
                stability = Stability.STABLE if runs >= 2 else Stability.UNCONFIRMED
                spread = []
        elif not condition_complete:
            # value moved AND the condition was truncated: the variation may be an artifact of
            # distinct inputs collapsing into one observed condition. Refuse to call it noise.
            stability = Stability.CAPTURE_GAP
        else:
            # value moved under a fully-observed identical input: distributional if a moving number
            # (frequency/latency-like), otherwise true noise to quotient out (timestamps, ids).
            if rep.kind == FactKind.FREQUENCY or _all_numeric(spread):
                stability = Stability.DISTRIBUTIONAL
            else:
                stability = Stability.NOISE

        culled.append(CulledFact(fact=rep, stability=stability, runs=runs,
                                 distinct_values=distinct, spread=spread))
    return VarianceReport(culled)
