"""v0.2 locks: nested by-path resolution, numeric coercion, accretive merge."""
import time
from veritas.models import (
    Expectation, Predicate, CodeAnchor, Observation, Invocation, EnvFingerprint,
    Kind, Source, Status, Grade, VerdictType,
)
from veritas.join import join, merge_observations


def _exp(pred, kind=Kind.RELATIONSHIP):
    return Expectation(id="exp_0001", claim="c", kind=kind,
                       anchor=CodeAnchor(symbol="com.x.Sel.pick"), predicate=pred,
                       source=Source.AGENT, status=Status.OPEN, grade=Grade.HYPOTHESIS)


def _obs(invs=None, methods=None, cfg_live=None, cfg_file=None, ts=None):
    return Observation(id=f"o{ts or 0}", fingerprint=EnvFingerprint(env="staging", timestamp=ts or time.time()),
                       methods_executed=methods or ["com.x.Sel.pick"], invocations=invs or [],
                       config_live=cfg_live or {}, config_file=cfg_file or {})


def test_nested_by_path_contradicted():
    inv = Invocation(method="com.x.Sel.pick",
                     args={"candidates": [{"id": "A", "breakdown": {"netPrice": 520}},
                                          {"id": "B", "breakdown": {"netPrice": 491}}]},
                     ret={"id": "A", "breakdown": {"netPrice": 520}})
    p = Predicate(kind=Kind.RELATIONSHIP, over="candidates", select="argmin", by="breakdown.netPrice",
                  equals="ret", method="com.x.Sel.pick", human="lowest net price wins")
    v = join(_exp(p), [_obs([inv])], env="staging")
    assert v.type == VerdictType.CONTRADICTED
    assert "491" in v.observed and "520" in v.observed


def test_nested_unverifiable_when_path_absent():
    # nested field not captured -> UNVERIFIABLE that names the unfold to add (never a guess)
    inv = Invocation(method="com.x.Sel.pick",
                     args={"candidates": [{"id": "A"}, {"id": "B"}]}, ret={"id": "A"})
    p = Predicate(kind=Kind.RELATIONSHIP, over="candidates", select="argmin", by="breakdown.netPrice",
                  equals="ret", method="com.x.Sel.pick", human="x")
    v = join(_exp(p), [_obs([inv])], env="staging")
    assert v.type == VerdictType.UNVERIFIABLE
    assert "unfold=breakdown.netPrice" in (v.missing or "")


def test_numeric_coercion_on_string_values():
    # by-values captured as strings must still order/compare numerically
    inv = Invocation(method="com.x.Sel.pick",
                     args={"candidates": [{"id": "A", "price": "549"}, {"id": "B", "price": "519"}]},
                     ret={"id": "B", "price": "519"})
    p = Predicate(kind=Kind.RELATIONSHIP, over="candidates", select="argmin", by="price",
                  equals="ret", method="com.x.Sel.pick", human="cheapest wins")
    v = join(_exp(p), [_obs([inv])], env="staging")
    assert v.type == VerdictType.CONFIRMED          # "519" < "549" numerically


def test_accretive_merge():
    # one observation has the invocation, another (later) has the live config -> merged join sees both
    inv = Invocation(method="com.x.Sel.pick",
                     args={"candidates": [{"id": "B", "price": 519}]}, ret={"id": "B", "price": 519})
    o1 = _obs([inv], cfg_file={"s.strategy": "cheapest"}, ts=1000)
    o2 = _obs([], methods=["com.x.Other.run"], cfg_live={"s.strategy": "v3"}, ts=2000)
    merged = merge_observations([o1, o2])
    assert "com.x.Sel.pick" in merged.methods_set() and "com.x.Other.run" in merged.methods_set()
    assert merged.config_live["s.strategy"] == "v3" and merged.invocations
    p = Predicate(kind=Kind.RELATIONSHIP, over="candidates", select="argmin", by="price",
                  equals="ret", method="com.x.Sel.pick", human="cheapest wins")
    v = join(_exp(p), [o1, o2], env="staging")
    assert v.type == VerdictType.CONFIRMED
    assert len(v.config_divergences) == 1           # divergence surfaced from the merged config


def test_sha_partition_no_false_contradicted_after_fix():
    # pre-fix (wrong) and post-fix (correct) traces in the same env. Verdict must follow the
    # LATEST git_sha only — a fixed bug must NOT still read CONTRADICTED. (v0.2 integrity fix.)
    cand = [{"id": "A", "price": 549}, {"id": "B", "price": 519}]
    bad = Invocation(method="com.x.Sel.pick", args={"candidates": cand}, ret={"id": "A", "price": 549})
    good = Invocation(method="com.x.Sel.pick", args={"candidates": cand}, ret={"id": "B", "price": 519})
    o_pre = Observation(id="pre", fingerprint=EnvFingerprint(env="staging", git_sha="AAA", timestamp=1000),
                        methods_executed=["com.x.Sel.pick"], invocations=[bad])
    o_post = Observation(id="post", fingerprint=EnvFingerprint(env="staging", git_sha="BBB", timestamp=2000),
                         methods_executed=["com.x.Sel.pick"], invocations=[good])
    p = Predicate(kind=Kind.RELATIONSHIP, over="candidates", select="argmin", by="price",
                  equals="ret", method="com.x.Sel.pick", human="cheapest wins")
    v = join(_exp(p), [o_pre, o_post], env="staging")
    assert v.type == VerdictType.CONFIRMED           # only sha BBB (fixed) is merged


def test_value_op_type_mismatch_no_crash():
    # captured-as-string field vs numeric literal under '<' must not raise (was a CLI crash)
    inv = Invocation(method="com.x.Sel.pick", args={}, ret={"price": "5400"})
    p = Predicate(kind=Kind.VALUE, field="ret.price", op="<", value=5100,
                  method="com.x.Sel.pick", human="price < 5100")
    v = join(_exp(p, kind=Kind.VALUE), [_obs([inv])], env="staging")
    assert v.type == VerdictType.CONTRADICTED        # 5400 (coerced) is not < 5100 — no crash
