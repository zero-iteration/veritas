"""The JOIN engine — Veritas's atomic operation and core IP.

Input: one Expectation + all Observations whose env/freshness qualify.
Output: one Verdict — CONFIRMED / CONTRADICTED / UNVERIFIABLE — plus two ambient
divergences computed on EVERY join independent of the predicate:
  * config divergence : file-declared vs observed-live config
  * path divergence    : expectation anchors that never executed under the scenario
Every line is provenance-tagged; UNVERIFIABLE always says exactly what would close it.
"""
from __future__ import annotations

from typing import Callable, Optional

from .models import (
    Expectation, Observation, Verdict, VerdictType, Confidence, Divergence, Kind,
)
from .predicates import evaluate, EvalResult
from .mechanism import mechanism_line


def config_divergences(obs: Observation) -> list[Divergence]:
    """File-declared vs observed-live config. The Cheapest-vs-V3 class of bug: trust observed."""
    out = []
    for key, file_val in obs.config_file.items():
        if key in obs.config_live and obs.config_live[key] != file_val:
            out.append(Divergence(
                kind="config",
                detail=f"{key}: file={file_val!r} but RUNTIME={obs.config_live[key]!r}  [trust observed]",
                file_value=file_val, live_value=obs.config_live[key],
            ))
    return out


def path_divergences(exp: Expectation, obs: Observation, anchor_method: Optional[str]) -> list[Divergence]:
    out = []
    if anchor_method and anchor_method not in obs.methods_set():
        out.append(Divergence(kind="path",
                              detail=f"anchor {anchor_method} was NOT executed under this scenario"))
    return out


def _anchor_method(exp: Expectation) -> Optional[str]:
    return exp.predicate.method or exp.anchor.symbol


def merge_observations(obs_list: list[Observation]) -> Observation:
    """Accretive merge (§3.2): combine qualifying observations into one — union of methods,
    all invocations, edges concatenated, config latest-wins. Coverage accretes instead of
    picking a single most-recent run (one scenario may execute the path, another the config)."""
    obs_list = sorted(obs_list, key=lambda o: o.fingerprint.timestamp)
    latest = obs_list[-1]
    methods: list[str] = []
    seen, invs, edges = set(), [], []
    cfg_live, cfg_file = {}, {}
    for o in obs_list:
        for m in o.methods_executed:
            if m not in seen:
                seen.add(m); methods.append(m)
        invs.extend(o.invocations); edges.extend(o.edges)
        cfg_live.update(o.config_live); cfg_file.update(o.config_file)   # asc ts -> latest wins
    return Observation(id=f"merged_{latest.fingerprint.env}", fingerprint=latest.fingerprint,
                       methods_executed=methods, edges=edges, invocations=invs,
                       config_live=cfg_live, config_file=cfg_file, trace_ref=latest.trace_ref)


def select_observation(exp: Expectation, observations: list[Observation],
                       env: Optional[str]) -> Optional[Observation]:
    cands = [o for o in observations if env is None or o.fingerprint.env == env]
    if not cands:
        return None
    # Partition by code version: merge only observations matching the LATEST git_sha, so a
    # pre-fix invocation can never contaminate a post-fix verdict (the one v0.2 integrity bug).
    # Fall back to merging all only when the latest observation has no sha.
    latest = max(cands, key=lambda o: o.fingerprint.timestamp)
    sha = latest.fingerprint.git_sha
    if sha:
        cands = [o for o in cands if o.fingerprint.git_sha == sha]
    return merge_observations(cands)


def join(exp: Expectation, observations: list[Observation], env: Optional[str] = None,
         mechanism_fn: Optional[Callable] = None) -> Verdict:
    am = _anchor_method(exp)
    obs = select_observation(exp, observations, env)

    if obs is None:
        return Verdict(
            type=VerdictType.UNVERIFIABLE, expectation_id=exp.id, env=env or "(any)",
            expected=exp.predicate.human or exp.claim,
            observed="no qualifying observation captured",
            confidence=Confidence.LOW,
            missing=f"run `veritas drive {exp.id}` to capture a scenario for env={env or 'staging'}",
            provenance={"observed": "none", "expected": exp.source.value},
        )

    res: EvalResult = evaluate(exp.predicate, obs)
    cfg = config_divergences(obs)
    path = path_divergences(exp, obs, am)

    if res.outcome is True:
        vtype, conf = VerdictType.CONFIRMED, Confidence.HIGH
    elif res.outcome is False:
        vtype, conf = VerdictType.CONTRADICTED, Confidence.HIGH
    else:
        vtype, conf = VerdictType.UNVERIFIABLE, Confidence.MEDIUM

    mech = None
    if vtype == VerdictType.CONTRADICTED:
        mech = (mechanism_fn or mechanism_line)(exp, obs, res)

    fp = obs.fingerprint
    n_inv = len(obs.invocations_of(am)) if am else len(obs.invocations)
    evidence = f"trace {obs.trace_ref or obs.id} (env {fp.env}), {n_inv} captured invocation(s) of {am}"

    prov = {
        "observed": f"capture@{fp.env}",
        "expected": exp.source.value,
        "config": "live-getter + file-declared",
        "mechanism": "static-graph" if mechanism_fn else "predicate-derived",
    }

    return Verdict(
        type=vtype, expectation_id=exp.id, env=fp.env,
        expected=exp.predicate.human or exp.claim,
        observed=res.detail, confidence=conf,
        observed_ts=fp.timestamp, sha=fp.git_sha, mechanism=mech,
        config_divergences=cfg, path_divergences=path,
        evidence=evidence, missing=res.missing if vtype == VerdictType.UNVERIFIABLE else None,
        provenance=prov,
    )
