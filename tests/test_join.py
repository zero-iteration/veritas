"""Core behavior lock: the join engine must be CONFIRMED/CONTRADICTED only on captured
values, UNVERIFIABLE otherwise (never a guess), and always surface config divergence."""
import time

from veritas.models import (
    Expectation, Predicate, CodeAnchor, Observation, Invocation, EnvFingerprint,
    Kind, Source, Status, Grade, VerdictType,
)
from veritas.join import join


def _obs(invs=None, methods=None, cfg_live=None, cfg_file=None, env="staging"):
    return Observation(
        id="obs_test", fingerprint=EnvFingerprint(env=env, git_sha="abc1234", timestamp=time.time()),
        methods_executed=methods or ["com.x.Sel.pick"], invocations=invs or [],
        config_live=cfg_live or {}, config_file=cfg_file or {},
    )


def _exp(pred, kind=Kind.RELATIONSHIP, anchor="com.x.Sel.pick"):
    return Expectation(id="exp_0001", claim="c", kind=kind, anchor=CodeAnchor(symbol=anchor),
                       predicate=pred, source=Source.AGENT, status=Status.OPEN, grade=Grade.HYPOTHESIS)


def test_contradicted_with_values():
    inv = Invocation(method="com.x.Sel.pick",
                     args={"candidates": [{"id": "A", "price": 549}, {"id": "B", "price": 519}]},
                     ret={"id": "A", "price": 549})
    p = Predicate(kind=Kind.RELATIONSHIP, over="candidates", select="argmin", by="price",
                  equals="ret", method="com.x.Sel.pick", human="cheapest wins")
    v = join(_exp(p), [_obs([inv])], env="staging")
    assert v.type == VerdictType.CONTRADICTED
    assert "549" in v.observed and "519" in v.observed


def test_confirmed_when_correct():
    inv = Invocation(method="com.x.Sel.pick",
                     args={"candidates": [{"id": "A", "price": 549}, {"id": "B", "price": 519}]},
                     ret={"id": "B", "price": 519})
    p = Predicate(kind=Kind.RELATIONSHIP, over="candidates", select="argmin", by="price",
                  equals="ret", method="com.x.Sel.pick", human="cheapest wins")
    v = join(_exp(p), [_obs([inv])], env="staging")
    assert v.type == VerdictType.CONFIRMED


def test_unverifiable_when_not_captured():
    # method executed but no invocation values captured -> never a guess
    p = Predicate(kind=Kind.RELATIONSHIP, over="candidates", select="argmin", by="price",
                  equals="ret", method="com.x.Sel.pick", human="cheapest wins")
    v = join(_exp(p), [_obs([], methods=["com.x.Sel.pick"])], env="staging")
    assert v.type == VerdictType.UNVERIFIABLE
    assert v.missing


def test_unverifiable_no_observation_for_env():
    p = Predicate(kind=Kind.PATH, method="com.x.Sel.pick", must="executed", human="runs")
    v = join(_exp(p, kind=Kind.PATH), [], env="prod")
    assert v.type == VerdictType.UNVERIFIABLE


def test_config_divergence_always_surfaced():
    inv = Invocation(method="com.x.Sel.pick",
                     args={"candidates": [{"id": "B", "price": 519}]}, ret={"id": "B", "price": 519})
    p = Predicate(kind=Kind.RELATIONSHIP, over="candidates", select="argmin", by="price",
                  equals="ret", method="com.x.Sel.pick", human="cheapest wins")
    obs = _obs([inv], cfg_live={"s.strategy": "v3"}, cfg_file={"s.strategy": "cheapest"})
    v = join(_exp(p), [obs], env="staging")
    assert v.type == VerdictType.CONFIRMED          # predicate holds...
    assert len(v.config_divergences) == 1           # ...but divergence is still reported
    assert v.config_divergences[0].live_value == "v3"


def test_config_kind_contradicted():
    p = Predicate(kind=Kind.CONFIG, key="s.strategy", op="==", value="cheapest", human="strategy is cheapest")
    obs = _obs(cfg_live={"s.strategy": "v3"}, cfg_file={"s.strategy": "cheapest"})
    v = join(_exp(p, kind=Kind.CONFIG), [obs], env="staging")
    assert v.type == VerdictType.CONTRADICTED
