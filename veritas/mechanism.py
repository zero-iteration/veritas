"""Mechanism line — the 'why it's coded this way' under a contradiction.

v1 derives a structural mechanism from the predicate + captured values. The richer
form (the actual comparator expression) is supplied by a static call/data-flow graph
via the `mechanism_fn` hook on join(); this is the documented integration point.
"""
from __future__ import annotations

from .models import Expectation, Observation, Kind
from .predicates import EvalResult


def mechanism_line(exp: Expectation, obs: Observation, res: EvalResult) -> str:
    p = exp.predicate
    v = res.values
    if p.kind == Kind.RELATIONSHIP:
        return (f"{p.method or exp.anchor.symbol} selects via {p.select}({p.by}); the returned element had "
                f"{p.by}={v.get('chosen_by')} while a candidate had {p.by}={v.get('best_by')} — the live "
                f"selection is not governed by {p.by} alone (a different field/factor drives the comparator).")
    if p.kind == Kind.VALUE:
        return (f"at {p.method or exp.anchor.symbol}, {p.field} resolved to {v.get('observed')} which violates "
                f"the required {p.op} {p.value}.")
    if p.kind == Kind.CONFIG:
        return (f"the live value of {p.key} is {v.get('live')!r}, not the expected {v.get('expected')!r} — "
                f"behavior follows the runtime key, not the file.")
    if p.kind == Kind.PATH:
        return f"{p.method} execution status diverged from the expectation under this scenario."
    return ""
