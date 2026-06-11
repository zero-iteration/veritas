"""Predicate evaluation — the deterministic core of the join.

A predicate evaluates to exactly one of: True (holds), False (violated), or None
(UNVERIFIABLE — the path/values/config needed weren't captured). None is never a
guess; it's the honesty valve that keeps Veritas from re-manufacturing confident-wrong.
Every result carries the actual deciding values for the verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .models import Predicate, Observation, Invocation


@dataclass
class EvalResult:
    outcome: Optional[bool]          # True holds / False violated / None unverifiable
    detail: str                      # human description of the deciding values
    values: dict                     # structured deciding values (provenance)
    missing: Optional[str] = None     # if unverifiable: exactly what would close the gap


# --------------------------------------------------------------------------- helpers
def _resolve(inv: Invocation, path: str) -> tuple[bool, Any]:
    """Resolve 'ret', 'ret.x', 'arg.name', 'arg.name.x' against a captured invocation."""
    parts = path.split(".")
    if parts[0] == "ret":
        cur: Any = inv.ret
        rest = parts[1:]
    elif parts[0] == "arg" and len(parts) >= 2:
        cur = inv.args.get(parts[1], _MISSING)
        rest = parts[2:]
    else:
        return False, None
    if cur is _MISSING:
        return False, None
    for p in rest:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return False, None
    return True, cur


_MISSING = object()


def _dig(d, dotted):
    """Resolve a dotted path inside a captured field-dict (e.g. 'breakdown.netPrice')."""
    cur = d
    for p in dotted.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return False, None
    return True, cur


def _numkey(v):
    """Numeric coercion guard (§3.2): order/compare by-field values numerically when possible."""
    if isinstance(v, bool):
        return (1, int(v))
    if isinstance(v, (int, float)):
        return (1, float(v))
    try:
        return (1, float(v))
    except (TypeError, ValueError):
        return (0, str(v))


def _eq(a, b):
    ka, kb = _numkey(a), _numkey(b)
    return ka == kb


_OPS = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b, ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b,
    "in": lambda a, b: a in b,
}


def _summ(d: Any) -> str:
    if isinstance(d, dict):
        return "{" + ", ".join(f"{k}={v}" for k, v in d.items()) + "}"
    return str(d)


# --------------------------------------------------------------------------- per-kind
def eval_relationship(pred: Predicate, invs: list[Invocation]) -> EvalResult:
    """argmin/argmax over a captured collection: the returned element must be the
    selected one (compared on the `by` field — robust to object identity)."""
    if not invs:
        return EvalResult(None, "anchor method captured no invocations", {},
                          missing=f"drive a scenario that executes {pred.method or 'the anchor'} so its args are captured")
    sel = (pred.select or "argmin").lower()
    by = pred.by
    for inv in invs:
        coll = inv.args.get(pred.over)
        if not isinstance(coll, list) or not coll:
            return EvalResult(None, f"collection arg '{pred.over}' not captured as a list", {},
                              missing=f"register field unfolding for '{pred.over}' elements (unfold={by})")
        elems = []
        for e in coll:
            ok, val = _dig(e, by) if isinstance(e, dict) else (False, None)
            if not ok:
                return EvalResult(None, f"field '{by}' not captured on '{pred.over}' elements", {},
                                  missing=f"add `unfold={by}` to the capture args so '{pred.over}' elements expose it")
            elems.append((val, e))
        ok_ret, ret_val = _resolve(inv, pred.equals or "ret")
        okb, ret_by = (_dig(ret_val, by) if (ok_ret and isinstance(ret_val, dict)) else (False, None))
        if not okb:
            return EvalResult(None, f"return value's '{by}' not captured", {},
                              missing=f"add `unfold={by}` so the returned element exposes it")
        best_by, best = (min if sel == "argmin" else max)(elems, key=lambda p: _numkey(p[0]))
        chosen_by = ret_by
        holds = _eq(chosen_by, best_by)
        if not holds:
            return EvalResult(
                False,
                f"chose {_summ(ret_val)} ({by}={chosen_by}) over {_summ(best)} ({by}={best_by})  "
                f"← {'cheaper' if sel=='argmin' else 'higher'} by {by}, lost",
                {"chosen": ret_val, "best_available": best, "by": by,
                 "chosen_by": chosen_by, "best_by": best_by},
            )
    rep_ret = invs[0].ret
    return EvalResult(True, f"returned element is the {sel} by {by} across {len(invs)} invocation(s) "
                      f"({_summ(rep_ret)})", {"by": by, "n": len(invs)})


def eval_value(pred: Predicate, invs: list[Invocation]) -> EvalResult:
    if not invs:
        return EvalResult(None, "anchor method captured no invocations", {},
                          missing="drive a scenario that executes the anchor so its values are captured")
    op = _OPS.get(pred.op or "==")
    if op is None:
        return EvalResult(None, f"unsupported op '{pred.op}'", {})
    for inv in invs:
        ok, val = _resolve(inv, pred.field or "ret")
        if not ok:
            return EvalResult(None, f"field '{pred.field}' not captured", {},
                              missing=f"add '{pred.field}' to the capture allowlist")
        if not op(val, pred.value):
            return EvalResult(False, f"{pred.field} = {val}, expected {pred.op} {pred.value}",
                              {"field": pred.field, "observed": val, "op": pred.op, "expected": pred.value})
    ok, val = _resolve(invs[0], pred.field or "ret")
    return EvalResult(True, f"{pred.field} {pred.op} {pred.value} holds (observed {val})",
                      {"field": pred.field, "observed": val})


def eval_config(pred: Predicate, obs: Observation) -> EvalResult:
    if pred.key not in obs.config_live:
        return EvalResult(None, f"config key '{pred.key}' not observed live", {},
                          missing=f"drive a scenario whose path reads '{pred.key}', or widen config capture")
    live = obs.config_live[pred.key]
    op = _OPS.get(pred.op or "==")
    holds = op(live, pred.value)
    detail = f"{pred.key} (live) = {live!r}, expected {pred.op} {pred.value!r}"
    return EvalResult(holds, detail, {"key": pred.key, "live": live, "op": pred.op, "expected": pred.value})


def eval_path(pred: Predicate, obs: Observation) -> EvalResult:
    if not obs.methods_executed:
        return EvalResult(None, "no methods captured in this observation", {},
                          missing="drive any scenario so execution is captured")
    executed = pred.method in obs.methods_set()
    want_exec = (pred.must or "executed") == "executed"
    holds = (executed == want_exec)
    detail = f"{pred.method} was {'executed' if executed else 'NOT executed'} (required: {pred.must or 'executed'})"
    return EvalResult(holds, detail, {"method": pred.method, "executed": executed, "must": pred.must})


def evaluate(pred: Predicate, obs: Observation) -> EvalResult:
    """Dispatch by kind. Anchor method for relationship/value comes from pred.method."""
    from .models import Kind
    if pred.kind == Kind.RELATIONSHIP:
        return eval_relationship(pred, obs.invocations_of(pred.method) if pred.method else obs.invocations)
    if pred.kind == Kind.VALUE:
        return eval_value(pred, obs.invocations_of(pred.method) if pred.method else obs.invocations)
    if pred.kind == Kind.CONFIG:
        return eval_config(pred, obs)
    if pred.kind == Kind.PATH:
        return eval_path(pred, obs)
    return EvalResult(None, f"unknown predicate kind {pred.kind}", {})
