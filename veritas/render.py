"""Render a Verdict as the canonical human/agent-facing block (~200-300 tokens)."""
from __future__ import annotations

import datetime as _dt
from .models import Verdict, VerdictType


def _date(ts):
    if not ts:
        return "?"
    return _dt.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")


def render_verdict(v: Verdict) -> str:
    sha = (v.sha or "")[:7]
    head = f"VERDICT: {v.type.value}  ({v.expectation_id}, observed under {v.env}, {_date(v.observed_ts)}"
    head += f", sha {sha}" if sha else ""
    head += ")"
    L = [head]
    L.append(f"  expected : {v.expected}")
    L.append(f"  observed : {v.observed}")
    if v.mechanism:
        L.append(f"  mechanism: {v.mechanism}")
    for d in v.config_divergences:
        L.append(f"  config   : {d.detail}")
    for d in v.path_divergences:
        L.append(f"  path     : {d.detail}")
    if v.evidence:
        L.append(f"  evidence : {v.evidence}")
    if v.type == VerdictType.UNVERIFIABLE and v.missing:
        L.append(f"  to verify: {v.missing}")
    L.append(f"  confidence: {v.confidence.value}")
    prov = ", ".join(f"{k}={val}" for k, val in v.provenance.items())
    if prov:
        L.append(f"  provenance: {prov}")
    return "\n".join(L)
