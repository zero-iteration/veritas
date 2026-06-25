"""Veritas core model — three first-class objects: Expectation, Observation, Verdict.

Everything in the product is a CRUD or a join over these. Each carries provenance;
nothing is fabricated — the inviolable property. All dataclasses are JSON-round-trippable
(the trace contract between the JVM capture agent and the join engine is plain JSON).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------- enums
class Kind(str, Enum):
    RELATIONSHIP = "relationship"   # argmin/argmax/compare over a captured collection
    VALUE = "value"                 # a captured field equals/in-range a literal
    CONFIG = "config"               # a live config key equals an expected value
    PATH = "path"                   # was this method actually executed


class Status(str, Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


class Grade(str, Enum):
    """E-tier hardening: a claim's epistemic standing, independent of any single verdict."""
    CONFIRMED = "confirmed"
    HYPOTHESIS = "hypothesis"       # ticket-mined / agent-guessed; never enters as truth
    UNVERIFIED = "unverified"


class VerdictType(str, Enum):
    CONFIRMED = "CONFIRMED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIABLE = "UNVERIFIABLE"


class Confidence(str, Enum):
    HIGH = "HIGH"       # values captured directly at the decision site
    MEDIUM = "MEDIUM"   # inferred / partial capture
    LOW = "LOW"


class Source(str, Enum):
    AGENT = "agent"
    HUMAN = "human"
    TICKET = "ticket"
    DOC = "doc"


# --------------------------------------------------------------------------- anchor
@dataclass
class CodeAnchor:
    """Where a claim is anchored in code, freshness-hashed so it self-expires on edit."""
    symbol: str                       # e.g. "com.example.RateSelector.pick"
    file: Optional[str] = None
    line: Optional[int] = None
    anchor_hash: Optional[str] = None  # hash of the anchored source span; None until resolved

    def to_dict(self) -> dict: return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "CodeAnchor": return CodeAnchor(**d)


# --------------------------------------------------------------------------- predicate
@dataclass
class Predicate:
    """Machine-checkable, agent-authored, human-readable claim form. One canonical
    structured shape per Kind (chosen over free-form expressions for ~100% precision —
    no parse ambiguity). `human` is the natural-language rendering shown to people.

    relationship: over + select(argmin|argmax|min|max) + by + equals(ret|ret.<f>)
    value:        field(ret.<f> | arg.<name>.<f>) + op(== != < > <= >= in) + value
    config:       key + op + value
    path:         method + must(executed|not_executed)
    """
    kind: Kind
    human: str = ""
    # relationship
    over: Optional[str] = None
    select: Optional[str] = None
    by: Optional[str] = None
    equals: Optional[str] = None
    # value
    field: Optional[str] = None
    op: Optional[str] = None
    value: Any = None
    # config
    key: Optional[str] = None
    # path
    method: Optional[str] = None
    must: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self); d["kind"] = self.kind.value; return {k: v for k, v in d.items() if v is not None}

    @staticmethod
    def from_dict(d: dict) -> "Predicate":
        d = dict(d); d["kind"] = Kind(d["kind"]); return Predicate(**d)


# --------------------------------------------------------------------------- expectation
@dataclass
class Expectation:
    id: str
    claim: str
    kind: Kind
    anchor: CodeAnchor
    predicate: Predicate
    source: Source = Source.AGENT
    status: Status = Status.OPEN
    grade: Grade = Grade.HYPOTHESIS
    ticket: Optional[str] = None
    created_ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "claim": self.claim, "kind": self.kind.value,
            "anchor": self.anchor.to_dict(), "predicate": self.predicate.to_dict(),
            "source": self.source.value, "status": self.status.value,
            "grade": self.grade.value, "ticket": self.ticket, "created_ts": self.created_ts,
        }

    @staticmethod
    def from_dict(d: dict) -> "Expectation":
        return Expectation(
            id=d["id"], claim=d["claim"], kind=Kind(d["kind"]),
            anchor=CodeAnchor.from_dict(d["anchor"]), predicate=Predicate.from_dict(d["predicate"]),
            source=Source(d.get("source", "agent")), status=Status(d.get("status", "open")),
            grade=Grade(d.get("grade", "hypothesis")), ticket=d.get("ticket"),
            created_ts=d.get("created_ts", time.time()),
        )


# --------------------------------------------------------------------------- observation
@dataclass
class Invocation:
    """One captured call of a method: unfolded argument fields + return fields.
    Values are decision-field values (primitives/boxed/String), NOT type names.

    `truncated` lists capture sites where a cap was hit (e.g. "candidates[0]: field cap 512").
    It is the LOUD half of capture completeness (§11.1): a dropped field collapses distinct
    inputs to one condition, so the engine must know the condition was only partially observed
    rather than silently treating it as determinism. Paths not prefixed "ret" truncate the args
    — i.e. they corrupt the condition itself, the worst case."""
    method: str
    args: dict[str, Any] = field(default_factory=dict)   # name -> scalar | {field:val} | [ {field:val}, ... ]
    ret: Any = None                                       # scalar | {field:val}
    truncated: list[str] = field(default_factory=list)    # capture sites where a cap dropped data

    def args_truncated(self) -> bool:
        """True if any truncation hit the arguments — i.e. the CONDITION may be incomplete."""
        return any(not t.split(":", 1)[0].strip().startswith("ret") for t in self.truncated)

    def to_dict(self) -> dict: return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Invocation":
        return Invocation(method=d["method"], args=d.get("args", {}), ret=d.get("ret"),
                          truncated=d.get("truncated", []))


@dataclass
class EnvFingerprint:
    env: str = "local"
    profile: Optional[str] = None
    git_sha: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    strategy_keys: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict: return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "EnvFingerprint": return EnvFingerprint(**d)


@dataclass
class Observation:
    """One captured execution of a scenario. Append-only, accretive, per-env."""
    id: str
    fingerprint: EnvFingerprint
    methods_executed: list[str] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)            # {"caller","callee","count"}
    invocations: list[Invocation] = field(default_factory=list)  # decision-site values
    config_live: dict[str, Any] = field(default_factory=dict)   # key -> observed-live value
    config_file: dict[str, Any] = field(default_factory=dict)   # key -> file-declared value (if known)
    effects: list[dict] = field(default_factory=list)           # {"op","resource","value","method"}
    trace_ref: Optional[str] = None

    def methods_set(self) -> set[str]: return set(self.methods_executed)

    def invocations_of(self, method: str) -> list[Invocation]:
        return [i for i in self.invocations if i.method == method]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "fingerprint": self.fingerprint.to_dict(),
            "methods_executed": self.methods_executed, "edges": self.edges,
            "invocations": [i.to_dict() for i in self.invocations],
            "config_live": self.config_live, "config_file": self.config_file,
            "effects": self.effects, "trace_ref": self.trace_ref,
        }

    @staticmethod
    def from_dict(d: dict) -> "Observation":
        return Observation(
            id=d["id"], fingerprint=EnvFingerprint.from_dict(d["fingerprint"]),
            methods_executed=d.get("methods_executed", []), edges=d.get("edges", []),
            invocations=[Invocation.from_dict(i) for i in d.get("invocations", [])],
            config_live=d.get("config_live", {}), config_file=d.get("config_file", {}),
            effects=d.get("effects", []), trace_ref=d.get("trace_ref"),
        )


# --------------------------------------------------------------------------- verdict
@dataclass
class Divergence:
    kind: str                 # "config" | "path"
    detail: str
    file_value: Any = None
    live_value: Any = None

    def to_dict(self) -> dict: return asdict(self)


@dataclass
class Verdict:
    """The product's atomic output — the join of one Expectation against Observations.
    Every line is provenance-tagged; nothing fabricated."""
    type: VerdictType
    expectation_id: str
    env: str
    expected: str
    observed: str
    confidence: Confidence
    observed_ts: Optional[float] = None
    sha: Optional[str] = None
    mechanism: Optional[str] = None
    config_divergences: list[Divergence] = field(default_factory=list)
    path_divergences: list[Divergence] = field(default_factory=list)
    evidence: Optional[str] = None
    missing: Optional[str] = None          # for UNVERIFIABLE: exactly what to run/scope to close it
    provenance: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type.value, "expectation_id": self.expectation_id, "env": self.env,
            "expected": self.expected, "observed": self.observed,
            "confidence": self.confidence.value, "observed_ts": self.observed_ts, "sha": self.sha,
            "mechanism": self.mechanism,
            "config_divergences": [d.to_dict() for d in self.config_divergences],
            "path_divergences": [d.to_dict() for d in self.path_divergences],
            "evidence": self.evidence, "missing": self.missing, "provenance": self.provenance,
        }
