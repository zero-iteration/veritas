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
from .observe import (
    build_baseline, check_run, accept_run, LedgerStore, DeltaItem, StubLabeler, ClaudeLabeler,
)


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

    # -- capture args derived from expectations (unblocks attach) ----------
    def capture_args(self) -> dict:
        """Derive the agent attach args from registered expectations: scope, the methods to
        deep-capture (Class.method), and the nested `unfold` field paths (§3.1-1)."""
        classes, methods, unfold = set(), set(), set()
        for e in self.exp.all():
            sym = e.predicate.method or e.anchor.symbol
            parts = sym.split(".")
            if len(parts) >= 2:
                classes.add(".".join(parts[:-1])); methods.add(".".join(parts[-2:]))
            by = e.predicate.by
            if by and "." in by:
                unfold.add(by)
            fld = e.predicate.field
            if fld:
                f = fld.split(".", 1)[1] if fld.split(".")[0] in ("ret", "arg") else fld
                if "." in f:
                    unfold.add(f)
        # scope = longest common package prefix of the anchored classes
        scope = ""
        if classes:
            segs = [c.split(".") for c in classes]
            common = []
            for tup in zip(*segs):
                if len(set(tup)) == 1:
                    common.append(tup[0])
                else:
                    break
            scope = ".".join(common) or sorted(classes)[0].rsplit(".", 1)[0]
        args = f"scope={scope};out=trace.json;captureValues={','.join(sorted(methods))}"
        if unfold:
            args += f";unfold={','.join(sorted(unfold))}"
        args += ";configGetter=<your property getter, e.g. getPropertyValue>"
        return {"scope": scope, "captureValues": sorted(methods), "unfold": sorted(unfold),
                "suggested_javaagent": f"-javaagent:veritas-agent.jar={args}"}

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
            "attach": self.capture_args(),     # how to instrument for the open expectations
            "gaps_to_fill": ["auth token", "cohort/segment data", "env-specific ids"],
            "safety": "read-only by default; write/order/payment paths require --allow-writes",
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

    # -- observation-first layer (baseline / check / accept / show) --------
    def _labeler(self, use_llm: bool):
        return ClaudeLabeler() if use_llm else StubLabeler()

    def observe_baseline(self, env: str = "staging", use_llm: bool = False, note: str = "") -> dict:
        """Build the baseline ledger L0 from the stored observations for `env` (ideally several
        repeats, so variance-culling can confirm determinism)."""
        store = LedgerStore(self.vdir)
        pool = self.obs.for_env(env)
        if not pool:
            return {"error": f"no observations for env={env}", "hint": "veritas ingest <trace> first"}
        res = build_baseline(pool, env=env, version=store.next_version(),
                             labeler=self._labeler(use_llm), note=note)
        store.save(res.ledger)
        return {"ledger": res.ledger.summary(), "variance": res.variance.summary()}

    def observe_check(self, trace_path: str, env: str = "staging",
                      delta: Optional[list[dict]] = None, config_file: Optional[str] = None) -> dict:
        """Verify a new run against the baseline given the PRD's declared observation delta.
        PASS iff every declared change happened and nothing else among watched facts moved."""
        store = LedgerStore(self.vdir)
        led = store.head()
        if led is None:
            return {"error": "no ledger", "hint": "run `veritas observe baseline` first"}
        cfg = _parse_config(config_file) if config_file else None
        obs = ingest_trace(json.load(open(trace_path)), cfg, env)
        items = [DeltaItem.from_dict(d) for d in (delta or [])]
        return check_run(led, [obs], env=env, delta=items).to_dict()

    def observe_accept(self, trace_paths: list[str], env: str = "staging",
                       use_llm: bool = False) -> dict:
        """Advance the baseline: L0 <- A. The accepted run becomes the next version."""
        store = LedgerStore(self.vdir)
        led = store.head()
        if led is None:
            return {"error": "no ledger", "hint": "run `veritas observe baseline` first"}
        obs = [ingest_trace(json.load(open(p)), None, env) for p in trace_paths]
        new = accept_run(store, led, obs, env=env, labeler=self._labeler(use_llm))
        return {"ledger": new.summary()}

    def observe_show(self) -> dict:
        led = LedgerStore(self.vdir).head()
        if led is None:
            return {"error": "no ledger", "hint": "run `veritas observe baseline` first"}
        return {"summary": led.summary(),
                "watched": [{"anchor": e.anchor, "value": e.fact.value,
                             "salience": e.label.salience.value, "name": e.label.name}
                            for e in led.watched()]}

    def observe_couplings(self, env: str = "staging") -> dict:
        """The coupling graph through shared external resources — observation-to-observation edges
        that method-anchored capture cannot see. Descriptive: what is observably coupled."""
        from .observe import couplings, coupling_edges, run_facts
        pool = self.obs.for_env(env)
        if not pool:
            return {"error": f"no observations for env={env}", "hint": "veritas ingest <trace> first"}
        facts = run_facts(pool, env)
        cs = [c for c in couplings(facts) if c.is_coupling]
        return {"couplings": [c.to_dict() for c in cs],
                "edges": [e.to_dict() for e in coupling_edges(facts)]}


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
