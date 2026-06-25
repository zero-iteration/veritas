"""The atomic unit of the observation-first layer: a `Fact` = (anchor, condition, value).

Veritas's existing `Observation` is a whole captured *execution* (the firehose). A `Fact`
is one field-level behavior extracted from it — the thing we actually compare, version, and
reason about. Code is the disposable transition function; the Fact is the unit of record.

  * anchor    — a cross-version-stable *semantic boundary* identity (NOT a code location).
                e.g. "com.x.RateSelector.pick#ret.breakdown.netPrice". Survives line moves
                and local renames because it is keyed by method symbol + output field path.
  * condition — the canonicalized input/context the fact fired under. Same condition across
                runs => the values are comparable; a different condition is a different row,
                never a divergence. This is what makes "everything else unchanged" checkable.
  * value     — the observed leaf value (scalar / count / bool / config value).
  * kind      — VALUE (deterministic output leaf) | FREQUENCY (call/edge count, a
                distributional candidate) | CONFIG (live config key) | PATH (method executed).

Projection is mechanical and total: an Observation maps to a deterministic set of Facts with
no model in the loop. Meaning (name/salience) is assigned later, in `label.py`, and never here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterator, Optional

from ..models import Observation


class FactKind(str, Enum):
    VALUE = "value"          # a deterministic leaf value at an output boundary (ret/state field)
    FREQUENCY = "frequency"  # a call/edge count — distributional candidate
    CONFIG = "config"        # a live config key's observed value
    PATH = "path"            # a method was executed (value is True)
    STATE = "state"          # a read/write at an EXTERNAL effect boundary (cache/db/queue/http)


# --------------------------------------------------------------------------- canonicalization
def canonical(value: Any) -> str:
    """Stable, order-insensitive-at-the-key-level string form of a value, for equality.

    This is the equivalence relation: two values are "the same behavior" iff their canonical
    forms match. Numbers are compared numerically (1 == 1.0), dict key order is irrelevant,
    list order is preserved (order can itself be behavior). Kept deliberately simple — the
    noise that this does NOT quotient out is removed empirically by variance-culling, not by
    guessing here.
    """
    return json.dumps(_canon(value), sort_keys=True, separators=(",", ":"), default=str)


def _canon(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return int(f) if f.is_integer() else f
    if isinstance(v, dict):
        return {str(k): _canon(v[k]) for k in v}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    return v


def condition_of(args: dict[str, Any]) -> str:
    """The condition is the canonical form of the inputs the fact fired under.

    A short content hash keeps it addressable; the readable sample is carried separately on
    the Fact for labeling/display. `"*"` is the universal condition for facts that are not
    input-scoped (PATH, CONFIG, FREQUENCY at the run level)."""
    if not args:
        return "*"
    return "c:" + hashlib.sha256(canonical(args).encode()).hexdigest()[:16]


def flatten_leaves(value: Any, prefix: str = "ret") -> Iterator[tuple[str, Any]]:
    """Walk a captured value (scalar / unfolded POJO dict / list) to dotted leaf paths.

    {"breakdown":{"netPrice":520}} -> ("ret.breakdown.netPrice", 520).
    Lists yield indexed leaves ("ret.items[0].price"); ordering noise, if any, is caught by
    variance-culling rather than assumed here."""
    if isinstance(value, dict):
        if not value:
            yield prefix, {}
            return
        for k in value:
            yield from flatten_leaves(value[k], f"{prefix}.{k}")
    elif isinstance(value, (list, tuple)):
        if not value:
            yield prefix, []
            return
        for i, x in enumerate(value):
            yield from flatten_leaves(x, f"{prefix}[{i}]")
    else:
        yield prefix, value


# --------------------------------------------------------------------------- fact
@dataclass
class Fact:
    anchor: str
    condition: str
    value: Any
    kind: FactKind
    method: Optional[str] = None          # source method symbol (provenance, NOT identity)
    path: Optional[str] = None            # field path within the boundary, e.g. "ret.price"
    env: str = "local"
    sample_condition: Optional[dict] = None  # readable sample of the input (display/labeling only)
    op: Optional[str] = None              # STATE only: "read" | "write" (coupling direction)
    resource: Optional[str] = None        # STATE only: the external resource, e.g. "redis:price:SKU-42"
    condition_complete: bool = True       # False if capture truncated the input -> condition is partial (§11.1)

    def key(self) -> tuple[str, str]:
        """Identity for grouping/diffing: a fact is the *same fact* across runs iff (anchor,
        condition) match. The value is what may move; the key is what stays put."""
        return (self.anchor, self.condition)

    def canonical_value(self) -> str:
        return canonical(self.value)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "Fact":
        d = dict(d)
        d["kind"] = FactKind(d["kind"])
        return Fact(**d)


# --------------------------------------------------------------------------- projection
def project(obs: Observation) -> list[Fact]:
    """Observation (one captured execution) -> the set of Facts it asserts. Mechanical, total,
    model-free. Same trace in => same facts out."""
    env = obs.fingerprint.env
    facts: list[Fact] = []

    # PATH — which methods executed (value True; absence is handled at diff time).
    for m in obs.methods_executed:
        facts.append(Fact(anchor=f"path:{m}", condition="*", value=True,
                          kind=FactKind.PATH, method=m, env=env))

    # FREQUENCY — call/edge counts. Distributional candidates; confirmed by variance-culling.
    for e in obs.edges:
        caller, callee, count = e.get("caller"), e.get("callee"), e.get("count")
        if caller is None or callee is None:
            continue
        facts.append(Fact(anchor=f"edge:{caller}->{callee}", condition="*", value=count,
                          kind=FactKind.FREQUENCY, method=caller, env=env))

    # VALUE — every output leaf of every invocation, scoped by the canonicalized input. If capture
    # truncated the args, the condition is only partially observed: mark it so variance-culling
    # cannot mistake a collapsed condition for determinism (§11.1).
    for inv in obs.invocations:
        cond = condition_of(inv.args)
        complete = not inv.args_truncated()
        for path, val in flatten_leaves(inv.ret, prefix="ret"):
            facts.append(Fact(anchor=f"{inv.method}#{path}", condition=cond, value=val,
                              kind=FactKind.VALUE, method=inv.method, path=path, env=env,
                              sample_condition=inv.args or None, condition_complete=complete))

    # CONFIG — live config values (run-scoped).
    for k, v in obs.config_live.items():
        facts.append(Fact(anchor=f"config:{k}", condition="*", value=v,
                          kind=FactKind.CONFIG, env=env))

    # STATE — reads/writes at external effect boundaries (cache/db/queue/http). The anchor is the
    # EXTERNAL resource name, not a code symbol — so two different methods touching the same
    # resource produce facts that SHARE an anchor, making coupling-through-state a join. The `#op`
    # suffix keeps a write's value distinct from a read's at the same resource; coupling strips it.
    for ef in obs.effects:
        res, op = ef.get("resource"), ef.get("op")
        if res is None or op is None:
            continue
        facts.append(Fact(anchor=f"res:{res}#{op}", condition="*", value=ef.get("value"),
                          kind=FactKind.STATE, method=ef.get("method"), env=env,
                          op=op, resource=res))

    return facts


def project_all(observations: list[Observation]) -> list[Fact]:
    out: list[Fact] = []
    for o in observations:
        out.extend(project(o))
    return out
