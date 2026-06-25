"""Observation-first layer: projection, variance-culling, ledger, and the mechanical
PRD-delta check — including the free dark-coupling regression catch."""
import json
import os
import time

from veritas.models import Observation, Invocation, EnvFingerprint
from veritas.stores import ingest_trace
from veritas.observe import (
    project, cull, Stability, StubLabeler, Salience, build_baseline, check_run, accept_run,
    DeltaItem, LedgerStore, Ledger, build_ledger, group_of, canonical,
)
from veritas.observe.facts import flatten_leaves, condition_of, FactKind, project_all
from veritas.observe.diff import diff, check
from veritas.observe import couplings, coupling_edges, blast_surface


# --------------------------------------------------------------------------- helpers
def _obs(invs=None, methods=None, edges=None, cfg_live=None, ts=0, env="staging", sha="S1"):
    return Observation(
        id=f"o{ts}", fingerprint=EnvFingerprint(env=env, git_sha=sha, timestamp=ts or time.time()),
        methods_executed=methods or [i.method for i in (invs or [])],
        edges=edges or [], invocations=invs or [], config_live=cfg_live or {})


def _pick(price, carrier="A"):
    cands = [{"carrier": "A", "price": 519}, {"carrier": "B", "price": 549}]
    return Invocation(method="com.x.Sel.pick", args={"candidates": cands},
                      ret={"carrier": carrier, "price": price, "breakdown": {"netPrice": price - 9}})


def _audit(amount):
    return Invocation(method="com.x.Audit.write", args={"event": "pick"}, ret={"logged_amount": amount})


# --------------------------------------------------------------------------- stage 1: projection
def test_flatten_leaves_nested_and_list():
    leaves = dict(flatten_leaves({"carrier": "A", "breakdown": {"netPrice": 510}}, "ret"))
    assert leaves["ret.carrier"] == "A"
    assert leaves["ret.breakdown.netPrice"] == 510
    listed = dict(flatten_leaves([{"p": 1}, {"p": 2}], "ret"))
    assert listed["ret[0].p"] == 1 and listed["ret[1].p"] == 2


def test_projection_anchors_and_condition():
    facts = project(_obs([_pick(519)]))
    anchors = {f.anchor for f in facts}
    assert "com.x.Sel.pick#ret.price" in anchors
    assert "com.x.Sel.pick#ret.breakdown.netPrice" in anchors
    assert "path:com.x.Sel.pick" in anchors
    price = next(f for f in facts if f.anchor == "com.x.Sel.pick#ret.price")
    assert price.value == 519 and price.kind == FactKind.VALUE
    # same input => same condition; the condition is the input, not the output
    assert price.condition == condition_of({"candidates": _pick(519).args["candidates"]})


# --------------------------------------------------------------------------- stage 2: variance
def test_variance_stable_vs_noise_vs_distributional():
    # same input twice, same output -> STABLE
    stable_facts = project_all([_obs([_pick(519)], ts=1), _obs([_pick(519)], ts=2)])
    rep = cull(stable_facts)
    price = next(c for c in rep.culled if c.fact.anchor == "com.x.Sel.pick#ret.price")
    assert price.stability == Stability.STABLE and price.runs == 2

    # same input, output moves, non-numeric -> NOISE
    noisy = [
        Invocation(method="com.x.T.f", args={"k": 1}, ret={"sessionId": "aaa"}),
        Invocation(method="com.x.T.f", args={"k": 1}, ret={"sessionId": "bbb"}),
    ]
    rep2 = cull(project_all([_obs([noisy[0]], ts=1), _obs([noisy[1]], ts=2)]))
    sid = next(c for c in rep2.culled if c.fact.path == "ret.sessionId")
    assert sid.stability == Stability.NOISE

    # frequency that moves across identical runs -> DISTRIBUTIONAL
    e1 = _obs([_pick(519)], edges=[{"caller": "m", "callee": "n", "count": 3}], ts=1)
    e2 = _obs([_pick(519)], edges=[{"caller": "m", "callee": "n", "count": 5}], ts=2)
    rep3 = cull(project_all([e1, e2]))
    edge = next(c for c in rep3.culled if c.fact.anchor == "edge:m->n")
    assert edge.stability == Stability.DISTRIBUTIONAL


def test_single_run_is_unconfirmed():
    rep = cull(project(_obs([_pick(519)])))
    price = next(c for c in rep.culled if c.fact.anchor == "com.x.Sel.pick#ret.price")
    assert price.stability == Stability.UNCONFIRMED  # honest: one run can't prove determinism


# --------------------------------------------------------------------------- stage 3: labeling boundary
def test_stub_labeler_assigns_meaning_only():
    facts = project(_obs([_pick(519)]))
    labels = StubLabeler().label(facts)
    price = labels["com.x.Sel.pick#ret.price"]
    assert price.salience == Salience.HIGH          # "price" -> revenue-critical
    # the labeler returns ONLY meaning — there is no value/verdict field to return
    assert set(price.to_dict().keys()) == {"name", "salience", "group", "reason"}


def test_group_collapses_key_family():
    assert group_of("config:price:SKU-42:2026-07-01") == "config:price:SKU-{n}:{date}"


# --------------------------------------------------------------------------- ledger
def test_build_ledger_whitelist_excludes_noise():
    facts = project_all([_obs([_pick(519)], ts=1), _obs([_pick(519)], ts=2)])
    res = build_baseline(_observations_for(facts), env="staging")
    # noise never enters; price is watched
    price = next(e for e in res.ledger.entries if e.anchor == "com.x.Sel.pick#ret.price")
    assert price.status == "watched"
    assert all(e.status != "watched" or e.confirmations >= 2 for e in res.ledger.entries)


def _observations_for(_facts):
    # build_baseline takes Observations, not facts; this keeps the two stable runs together
    return [_obs([_pick(519)], ts=1), _obs([_pick(519)], ts=2)]


def test_ledger_round_trip():
    res = build_baseline([_obs([_pick(519)], ts=1), _obs([_pick(519)], ts=2)], env="staging")
    d = res.ledger.to_dict()
    back = Ledger.from_dict(json.loads(json.dumps(d)))
    assert back.version == res.ledger.version
    assert {e.anchor for e in back.entries} == {e.anchor for e in res.ledger.entries}


# --------------------------------------------------------------------------- the diff + PRD check
def _baseline():
    return build_baseline(
        [_obs([_pick(519), _audit(100)], ts=1), _obs([_pick(519), _audit(100)], ts=2)],
        env="staging").ledger


def test_unchanged_run_passes_clean():
    led = _baseline()
    report = check_run(led, [_obs([_pick(519), _audit(100)], ts=3)], env="staging", delta=[])
    assert report.verdict == "PASS"
    assert not report.regressions and not report.intended


def test_known_but_unwatched_facts_do_not_resurface_as_new():
    # config (run-scoped, unconfirmed) and a low-salience field are in the ledger but NOT watched.
    # Re-observing them unchanged must NOT be reported as `new` — that was a real bug.
    sess = Invocation(method="com.x.S.f", args={"k": 1}, ret={"id": "abc"})  # low-salience "id"
    runs = [_obs([_pick(519), sess], cfg_live={"flag.x": "on"}, ts=1),
            _obs([_pick(519), sess], cfg_live={"flag.x": "on"}, ts=2)]
    led = build_baseline(runs, env="staging").ledger
    report = check_run(led, [_obs([_pick(519), sess], cfg_live={"flag.x": "on"}, ts=3)],
                       env="staging", delta=[])
    assert report.verdict == "PASS"
    assert not report.regressions


def test_declared_change_passes():
    led = _baseline()
    # PRD declares the FULL price change: price and its coupled net price both move. Declaring
    # only one would (correctly) flag the other — that is the minimality principle enforcing itself.
    delta = [DeltaItem(anchor="com.x.Sel.pick#ret.price", expect="value", value=549),
             DeltaItem(anchor="com.x.Sel.pick#ret.breakdown.netPrice", expect="value", value=540)]
    report = check_run(led, [_obs([_pick(549), _audit(100)], ts=3)], env="staging", delta=delta)
    assert report.verdict == "PASS"
    assert any(c.after == 549 for c in report.intended)


def test_free_dark_coupling_regression_catch():
    # THE payoff: PRD declares only the price change. The audit amount ALSO moves (shared state),
    # which nobody declared. No dependency graph was built — we just watched the observation.
    led = _baseline()
    # the full intended price change is declared (price + net price); the audit is NOT
    delta = [DeltaItem(anchor="com.x.Sel.pick#ret.price", expect="value", value=549),
             DeltaItem(anchor="com.x.Sel.pick#ret.breakdown.netPrice", expect="value", value=540)]
    a = [_obs([_pick(549), _audit(549)], ts=3)]   # audit silently moved 100 -> 549
    report = check_run(led, a, env="staging", delta=delta)
    assert report.verdict == "REGRESSION"
    # the audit is the LONE undeclared mover — caught with no dependency graph, just by watching it
    assert [c.anchor for c in report.regressions] == ["com.x.Audit.write#ret.logged_amount"]
    # and the intended change is still recognized as intended, not lumped in
    assert any(c.anchor == "com.x.Sel.pick#ret.price" for c in report.intended)


def test_incomplete_when_declared_change_absent():
    led = _baseline()
    delta = [DeltaItem(anchor="com.x.Sel.pick#ret.price", expect="value", value=549)]
    report = check_run(led, [_obs([_pick(519), _audit(100)], ts=3)], env="staging", delta=delta)
    assert report.verdict == "INCOMPLETE"
    assert report.missing and report.missing[0].value == 549


def test_gone_with_coverage_gap_is_not_regression():
    led = _baseline()
    # A exercises only pick; the audit path never ran -> its absence is a coverage gap, not a regression
    report = check_run(led, [_obs([_pick(519)], ts=3)], env="staging", delta=[])
    assert report.verdict == "PASS"
    assert any(c.anchor == "com.x.Audit.write#ret.logged_amount" for c in report.coverage_gaps)


# --------------------------------------------------------------------------- accept advances baseline
def test_accept_advances_and_carries_labels(tmp_path):
    store = LedgerStore(str(tmp_path / ".veritas"))
    led1 = _baseline()
    store.save(led1)
    led2 = accept_run(store, led1, [_obs([_pick(549), _audit(549)], ts=5),
                                    _obs([_pick(549), _audit(549)], ts=6)], env="staging")
    assert led2.version == led1.version + 1
    # the accepted run becomes the new baseline: 549 now passes clean
    report = check_run(led2, [_obs([_pick(549), _audit(549)], ts=7)], env="staging", delta=[])
    assert report.verdict == "PASS"
    # labels carried forward (price still HIGH, same name object semantics)
    price = next(e for e in led2.entries if e.anchor == "com.x.Sel.pick#ret.price")
    assert price.label.salience == Salience.HIGH
    assert os.path.exists(store.head_path)


# --------------------------------------------------------------------------- effect boundaries / coupling
# A dark coupling: PriceWriter.store writes the target price AND a shared pricing counter; a
# different method, CheckoutCalc.compute, reads that counter. Method-anchored capture sees two
# unrelated methods. Effect-anchored capture sees the shared resource.
def _eff_obs(price, version, ts):
    return Observation(
        id=f"e{ts}", fingerprint=EnvFingerprint(env="staging", git_sha="S1", timestamp=ts),
        methods_executed=["com.x.PriceWriter.store", "com.x.CheckoutCalc.compute"],
        effects=[
            {"op": "write", "resource": "redis:price:SKU-42", "value": price, "method": "com.x.PriceWriter.store"},
            {"op": "write", "resource": "redis:pricing:version", "value": version, "method": "com.x.PriceWriter.store"},
            {"op": "read", "resource": "redis:pricing:version", "value": version, "method": "com.x.CheckoutCalc.compute"},
        ])


def test_effect_projects_to_resource_anchor():
    facts = project(_eff_obs(5499, 7, ts=1))
    price = next(f for f in facts if f.anchor == "res:redis:price:SKU-42#write")
    assert price.kind == FactKind.STATE and price.op == "write" and price.value == 5499
    assert price.resource == "redis:price:SKU-42"
    # the anchor is the EXTERNAL name, not a code symbol
    assert "PriceWriter" not in price.anchor


def test_coupling_through_shared_resource():
    facts = project(_eff_obs(5499, 7, ts=1))
    edges = coupling_edges(facts)
    # the edge method-anchored capture cannot make: writer -> reader through a shared cache key
    assert any(e.writer == "com.x.PriceWriter.store" and e.reader == "com.x.CheckoutCalc.compute"
               and e.resource == "redis:pricing:version" for e in edges)
    # the price key is write-only here -> not a coupling (nothing reads it back)
    price = next(c for c in couplings(facts) if c.resource == "redis:price:SKU-42")
    assert not price.is_coupling


def test_method_capture_alone_finds_no_coupling():
    # the same two methods, captured only as method invocations (no effects) -> zero shared anchors.
    # This is exactly what the original method-anchored model could see: nothing.
    obs = _obs([Invocation(method="com.x.PriceWriter.store", args={}, ret={"ok": True}),
                Invocation(method="com.x.CheckoutCalc.compute", args={}, ret={"total": 100})])
    assert coupling_edges(project(obs)) == []


def test_dark_coupling_regression_caught_at_resource():
    # baseline (x2 for stability): price=5499, shared version=7
    led = build_baseline([_eff_obs(5499, 7, ts=1), _eff_obs(5499, 7, ts=2)], env="staging").ledger
    # PRD declares ONLY the price change. The shared pricing counter is bumped as a side effect,
    # and CheckoutCalc's read of it moves — neither declared. No dependency graph was built.
    delta = [DeltaItem(anchor="res:redis:price:SKU-42#write", expect="value", value=5999)]
    report = check_run(led, [_eff_obs(5999, 8, ts=3)], env="staging", delta=delta)
    assert report.verdict == "REGRESSION"
    reg_resources = {c.anchor for c in report.regressions}
    assert "res:redis:pricing:version#write" in reg_resources   # the shared write moved, undeclared
    assert "res:redis:pricing:version#read" in reg_resources    # and propagated to the reader
    assert any(c.anchor == "res:redis:price:SKU-42#write" for c in report.intended)
    # the blast surface names who is observably downstream of the perturbed resource
    facts = project(_eff_obs(5999, 8, ts=3))
    assert blast_surface(facts, "redis:pricing:version") == ["com.x.CheckoutCalc.compute"]


# --------------------------------------------------------------------------- §11.1 capture completeness
def _gap_obs(carrier, truncated, ts):
    inv = Invocation(method="com.x.Sel.pick", args={"cands": [{"id": "X"}]},
                     ret={"carrier": carrier}, truncated=truncated)
    return Observation(id=f"g{ts}", fingerprint=EnvFingerprint(env="staging", git_sha="S1", timestamp=ts),
                       methods_executed=["com.x.Sel.pick"], invocations=[inv])


def test_truncated_condition_becomes_capture_gap_not_noise():
    # The §11.1 false-negative: the field that decides `carrier` was truncated out of the args, so
    # two distinct real inputs collapse to one observed condition and the deterministic carrier looks
    # like it "varies." WITHOUT the marker this is silently branded NOISE and dropped — catastrophic.
    # WITH the marker the engine refuses to judge it and raises a loud CAPTURE_GAP instead.
    truncated = ["cands[0]: field cap 512 (700 fields)"]
    rep = cull(project_all([_gap_obs("A", truncated, 1), _gap_obs("B", truncated, 2)]))
    cf = next(c for c in rep.culled if c.fact.anchor == "com.x.Sel.pick#ret.carrier")
    assert cf.stability == Stability.CAPTURE_GAP
    assert "A" in cf.spread and "B" in cf.spread


def test_same_variation_without_truncation_is_noise():
    # identical scenario but fully captured -> the variation is real noise, correctly quotiented out
    rep = cull(project_all([_gap_obs("A", [], 1), _gap_obs("B", [], 2)]))
    cf = next(c for c in rep.culled if c.fact.anchor == "com.x.Sel.pick#ret.carrier")
    assert cf.stability == Stability.NOISE


def test_capture_gap_recorded_loud_not_watched():
    truncated = ["cands[0]: field cap 512"]
    res = build_baseline([_gap_obs("A", truncated, 1), _gap_obs("B", truncated, 2)], env="staging")
    entry = next(e for e in res.ledger.entries if e.anchor == "com.x.Sel.pick#ret.carrier")
    assert entry.status == "capture_gap"     # present and loud (not dropped like noise)...
    assert entry.status != "watched"         # ...but never trusted as a verified observation
    assert res.variance.summary().get("capture_gap") == 1


def test_ret_only_truncation_does_not_poison_condition():
    # truncation in the RETURN is not in the condition; a stable input must still be judgeable
    t = ["ret.breakdown: field cap"]
    rep = cull(project_all([_gap_obs("A", t, 1), _gap_obs("A", t, 2)]))
    cf = next(c for c in rep.culled if c.fact.anchor == "com.x.Sel.pick#ret.carrier")
    assert cf.stability == Stability.STABLE


# --------------------------------------------------------------------------- real capture shape
def test_end_to_end_on_real_sample_trace():
    here = os.path.dirname(__file__)
    trace_path = os.path.join(here, "..", "samples", "checkout-demo", "trace.json")
    trace = json.load(open(trace_path))
    obs = ingest_trace(trace, env_override="staging")
    facts = project(obs)
    # the real trace has 3 identical pick invocations -> price is STABLE under one trace
    rep = cull(facts)
    price = next((c for c in rep.culled if c.fact.anchor.endswith("pick#ret.price")), None)
    assert price is not None and price.stability == Stability.STABLE and price.runs == 3
    # redacted PII is captured but the labeler marks it low-salience (won't alarm)
    labels = StubLabeler().label(facts)
    email = next((a for a in labels if "customerEmail" in a), None)
    if email:
        assert labels[email].salience == Salience.LOW
