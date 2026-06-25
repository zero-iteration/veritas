"""Persistence: the oracle (expectations) and observation store, under .veritas/.

Oracle = code-anchored, self-invalidating expectation corpus (the accreting moat).
Observations = append-only, accretive, per-env captured executions.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from .models import Expectation, Observation, EnvFingerprint, Invocation, Status, Grade
from . import anchors


class ExpectationStore:
    def __init__(self, vdir: str):
        self.path = os.path.join(vdir, "expectations.json")
        os.makedirs(vdir, exist_ok=True)
        self._d: dict[str, dict] = {}
        if os.path.exists(self.path):
            self._d = json.load(open(self.path))

    def _save(self):
        json.dump(self._d, open(self.path, "w"), indent=2)

    def next_id(self) -> str:
        n = 1 + max((int(k.split("_")[1]) for k in self._d if k.startswith("exp_")), default=0)
        return f"exp_{n:04d}"

    def add(self, exp: Expectation):
        self._d[exp.id] = exp.to_dict(); self._save()

    def get(self, eid: str) -> Optional[Expectation]:
        d = self._d.get(eid)
        return Expectation.from_dict(d) if d else None

    def all(self) -> list[Expectation]:
        return [Expectation.from_dict(d) for d in self._d.values()]

    def set_status(self, eid: str, status: Status):
        if eid in self._d:
            self._d[eid]["status"] = status.value; self._save()

    def set_grade(self, eid: str, grade: Grade):
        if eid in self._d:
            self._d[eid]["grade"] = grade.value; self._save()

    def prune_stale(self, roots: list[str]) -> list[str]:
        """Expire expectations whose anchored code changed. Returns the stale ids."""
        stale = []
        for eid, d in self._d.items():
            exp = Expectation.from_dict(d)
            if anchors.is_stale(exp.anchor, roots):
                d["status"] = Status.UNVERIFIABLE.value
                d["_stale"] = True
                stale.append(eid)
        if stale:
            self._save()
        return stale


class ObservationStore:
    def __init__(self, vdir: str):
        self.dir = os.path.join(vdir, "observations")
        os.makedirs(self.dir, exist_ok=True)

    def add(self, obs: Observation):
        json.dump(obs.to_dict(), open(os.path.join(self.dir, f"{obs.id}.json"), "w"), indent=2)

    def all(self) -> list[Observation]:
        out = []
        for fn in sorted(os.listdir(self.dir)):
            if fn.endswith(".json"):
                out.append(Observation.from_dict(json.load(open(os.path.join(self.dir, fn)))))
        return out

    def get(self, oid: str) -> Optional[Observation]:
        p = os.path.join(self.dir, f"{oid}.json")
        return Observation.from_dict(json.load(open(p))) if os.path.exists(p) else None

    def for_env(self, env: str) -> list[Observation]:
        return [o for o in self.all() if o.fingerprint.env == env]


def ingest_trace(trace: dict, config_file: Optional[dict] = None, env_override: Optional[str] = None,
                 obs_id: Optional[str] = None) -> Observation:
    """Parse the JVM capture agent's trace JSON (the contract) into an Observation.

    Trace shape:
      { "fingerprint": {...}, "methods": [...], "edges": [...],
        "invocations": [{"method","args","ret"}], "config_live": {k:v} }
    """
    fp_raw = dict(trace.get("fingerprint", {}))
    if env_override:
        fp_raw["env"] = env_override
    fp = EnvFingerprint(
        env=fp_raw.get("env", "local"), profile=fp_raw.get("profile"),
        git_sha=fp_raw.get("git_sha"), timestamp=fp_raw.get("timestamp", time.time()),
        strategy_keys=fp_raw.get("strategy_keys", {}),
    )
    invs = [Invocation.from_dict(i) for i in trace.get("invocations", [])]
    oid = obs_id or f"obs_{int(fp.timestamp)}_{fp.env}"
    return Observation(
        id=oid, fingerprint=fp,
        methods_executed=trace.get("methods", []),
        edges=trace.get("edges", []),
        invocations=invs,
        config_live=trace.get("config_live", {}),
        config_file=config_file or trace.get("config_file", {}),
        effects=trace.get("effects", []),
        trace_ref=trace.get("trace_ref", oid),
    )
