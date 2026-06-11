"""Application layer — the operations behind the 5 tools (CLI + MCP are thin over this)."""
from __future__ import annotations

import fnmatch
import json
import os
import time
from typing import Any, Optional

from . import anchors
from .models import (
    Expectation, Predicate, CodeAnchor, Kind, Source, Status, Grade, Observation, Verdict,
    VerdictType, Confidence,
)
from .stores import ExpectationStore, ObservationStore, ingest_trace
from .join import join, config_divergences
from .predicates import evaluate


class Workspace:
    def __init__(self, root: str = "."):
        self.root = os.path.abspath(root)
        self.vdir = os.path.join(self.root, ".veritas")
        os.makedirs(self.vdir, exist_ok=True)
        self.exp = ExpectationStore(self.vdir)
        self.obs = ObservationStore(self.vdir)
        cfgp = os.path.join(self.vdir, "config.json")
        self.cfg = json.load(open(cfgp)) if os.path.exists(cfgp) else {}
        self.roots = [os.path.join(self.root, r) for r in self.cfg.get("repo_roots", ["."])]

    # -- expectations -------------------------------------------------------
    def create_expectation(self, claim: str, kind: str, anchor_symbol: str, predicate: dict,
                           source: str = "agent", ticket: Optional[str] = None) -> Expectation:
        anchor = anchors.resolve(anchor_symbol, self.roots) or CodeAnchor(symbol=anchor_symbol)
        pred = Predicate.from_dict({**predicate, "kind": kind})
        if pred.method is None and kind in ("relationship", "value"):
            pred.method = anchor_symbol
        src = Source(source)
        grade = Grade.HYPOTHESIS if src in (Source.AGENT, Source.TICKET) else Grade.UNVERIFIED
        exp = Expectation(id=self.exp.next_id(), claim=claim, kind=Kind(kind), anchor=anchor,
                          predicate=pred, source=src, status=Status.OPEN, grade=grade, ticket=ticket)
        self.exp.add(exp)
        return exp

    # -- verify (headline) --------------------------------------------------
    def verify(self, eid: str, env: Optional[str] = None) -> Verdict:
        self.exp.prune_stale(self.roots)
        exp = self.exp.get(eid)
        if exp is None:
            raise KeyError(eid)
        v = join(exp, self.obs.all(), env)
        # write verdict back onto the expectation's status / grade
        if v.type == VerdictType.CONFIRMED:
            self.exp.set_status(eid, Status.CONFIRMED); self.exp.set_grade(eid, Grade.CONFIRMED)
        elif v.type == VerdictType.CONTRADICTED:
            self.exp.set_status(eid, Status.CONTRADICTED)
        else:
            self.exp.set_status(eid, Status.UNVERIFIABLE)
        return v

    def verify_claim(self, claim: str, kind: str, anchor_symbol: str, predicate: dict,
                     env: Optional[str] = None, source: str = "agent") -> Verdict:
        exp = self.create_expectation(claim, kind, anchor_symbol, predicate, source)
        return self.verify(exp.id, env)

    # -- observed config guard ---------------------------------------------
    def observed_config(self, key_glob: str, env: Optional[str] = None) -> dict:
        pool = self.obs.for_env(env) if env else self.obs.all()
        if not pool:
            return {"error": "no observations", "hint": "veritas drive / ingest a trace first"}
        obs = max(pool, key=lambda o: o.fingerprint.timestamp)
        keys = set(obs.config_live) | set(obs.config_file)
        out = {}
        for k in sorted(keys):
            if fnmatch.fnmatch(k, key_glob):
                fv, lv = obs.config_file.get(k), obs.config_live.get(k)
                out[k] = {"file": fv, "live": lv, "divergent": (k in obs.config_file and k in obs.config_live and fv != lv)}
        return {"env": obs.fingerprint.env, "keys": out,
                "divergent_count": sum(1 for v in out.values() if v["divergent"])}

    # -- explain ------------------------------------------------------------
    def explain(self, symbol: str, env: Optional[str] = None) -> dict:
        pool = self.obs.for_env(env) if env else self.obs.all()
        obs = max(pool, key=lambda o: o.fingerprint.timestamp) if pool else None
        actual = {"executed": False, "invocations": []}
        divergence = {"config": [], "path": []}
        if obs:
            actual["executed"] = symbol in obs.methods_set()
            actual["invocations"] = [i.to_dict() for i in obs.invocations_of(symbol)][:3]
            divergence["config"] = [d.to_dict() for d in config_divergences(obs)]
            if not actual["executed"]:
                divergence["path"] = [f"{symbol} not executed under latest {obs.fingerprint.env} trace"]
        expected = [{"id": e.id, "claim": e.claim, "status": e.status.value, "grade": e.grade.value}
                    for e in self.exp.all() if e.anchor.symbol == symbol or e.predicate.method == symbol]
        return {"symbol": symbol, "ACTUAL": actual, "EXPECTED": expected, "DIVERGENCE": divergence}

    # -- diff (fix verification) -------------------------------------------
    def diff(self, before_id: str, after_id: str) -> dict:
        a, b = self.obs.get(before_id), self.obs.get(after_id)
        if not a or not b:
            return {"error": "observation not found"}
        ma, mb = a.methods_set(), b.methods_set()
        changes = []
        # value changes at common invocation sites (compare return `by`-ish fields)
        amap = {i.method: i for i in a.invocations}
        for i in b.invocations:
            j = amap.get(i.method)
            if j and j.ret != i.ret:
                changes.append({"method": i.method, "before": j.ret, "after": i.ret})
        return {
            "paths_added": sorted(mb - ma), "paths_removed": sorted(ma - mb),
            "value_changes": changes,
            "summary": f"{len(changes)} decision value(s) changed, "
                       f"+{len(mb-ma)}/-{len(ma-mb)} paths between {before_id} and {after_id}",
        }

    # -- drive (HITL scaffold) ---------------------------------------------
    def drive(self, scenario_or_ticket: str, env: str = "staging") -> dict:
        """v1 keeps the human in the loop. Drafts a scenario request from the static
        endpoint list (if present) and names the un-inventable gaps to fill, then the
        caller fires it at an instrumented instance and ingests the trace."""
        endpoints = self.cfg.get("endpoints", [])
        draft = {
            "scenario": scenario_or_ticket, "env": env,
            "suggested_request": endpoints[0] if endpoints else
                {"method": "GET", "path": "<choose from `veritas endpoints`>", "body": None},
            "gaps_to_fill": ["auth token", "cohort/route data", "env-specific ids"],
            "safety": "read-only by default; write/booking/payment paths require --allow-writes",
            "next": "fire at an instrumented instance, then `veritas ingest <trace.json>` to auto-join",
        }
        return draft

    # -- ingest -------------------------------------------------------------
    def ingest(self, trace_path: str, config_file: Optional[str] = None,
               env: Optional[str] = None) -> Observation:
        trace = json.load(open(trace_path))
        cfg = _parse_config(config_file) if config_file else None
        obs = ingest_trace(trace, cfg, env)
        self.obs.add(obs)
        return obs


def _parse_config(path: str) -> dict:
    """Parse a .properties or simple flat .yml/.yaml file into key->value (file-declared)."""
    out: dict[str, Any] = {}
    if not os.path.exists(path):
        return out
    text = open(path).read()
    if path.endswith((".properties", ".env")):
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = _coerce(v.strip())
    else:  # naive single-level yaml: "key: value"
        for line in text.splitlines():
            if ":" in line and not line.strip().startswith("#"):
                k, v = line.split(":", 1)
                if v.strip():
                    out[k.strip()] = _coerce(v.strip())
    return out


def _coerce(v: str):
    v = v.strip().strip('"').strip("'")
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v
