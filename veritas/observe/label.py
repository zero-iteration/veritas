"""Stage 3 — meaning assignment. The ONLY place an LLM is allowed, and only for meaning.

Variance-culling gives a clean but anonymous set of stable facts: `com.x.Sel.pick#ret.price = 549`.
This stage assigns the human/business layer — a name, a salience (does this matter?), and a
group (collapse `price:SKU-42`, `price:SKU-99` into the family `price:SKU-{n}`).

The inviolable boundary, enforced by these types:
  * a Labeler receives Facts and returns Labels keyed by anchor.
  * a Label carries ONLY name / salience / group / reason.
  * it can NEVER produce or alter a value, and NEVER render a verdict (pass/fail).
A labeler being wrong is recoverable (a mislabeled salience just isn't watched yet, and the
first incident promotes it). A labeler touching values or verdicts would reintroduce the
hallucinated oracle the whole system exists to avoid — so the type makes it impossible.

`StubLabeler` is deterministic and hermetic (no API) so the core builds and tests without a key.
`ClaudeLabeler` is the real meaning engine, optional and behind an import guard.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Protocol

from .facts import Fact, FactKind


class Salience(str, Enum):
    HIGH = "high"      # revenue/correctness-critical surface — watch strictly
    MEDIUM = "medium"  # real behavior, watch
    LOW = "low"        # incidental / PII / cosmetic — capture but don't alarm on


@dataclass
class Label:
    name: str
    salience: Salience
    group: str          # the key-family this fact belongs to (anchors collapse into groups)
    reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["salience"] = self.salience.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "Label":
        return Label(name=d["name"], salience=Salience(d["salience"]),
                     group=d.get("group", ""), reason=d.get("reason", ""))


class Labeler(Protocol):
    def label(self, facts: list[Fact]) -> dict[str, Label]:
        """anchor -> Label. Pure meaning; no value or verdict may be returned."""
        ...


# --------------------------------------------------------------------------- grouping (shared)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUM = re.compile(r"\d+")


def group_of(anchor: str) -> str:
    """Collapse key-instances into a key-family: the right node for coupling/diff is the family
    (`config:price:SKU-{n}`), the instance is only for comparing values. Deterministic so both
    labelers agree on the family even when names differ. Date before number so a date doesn't
    shred into digit tokens."""
    g = _DATE.sub("{date}", anchor)
    g = _NUM.sub("{n}", g)
    return g


# --------------------------------------------------------------------------- stub (hermetic)
_HIGH = ("price", "amount", "total", "cost", "charge", "margin", "netprice",
         "balance", "payment", "refund", "discount", "tax", "selected", "carrier")
_LOW = ("email", "phone", "ssn", "token", "id", "uuid", "timestamp", "time", "date",
        "redacted", "session", "trace")


class StubLabeler:
    """Deterministic heuristic labeler — no network, no key. Good enough to exercise the whole
    pipeline and to be the offline fallback; not a substitute for the real meaning engine."""

    def label(self, facts: list[Fact]) -> dict[str, Label]:
        out: dict[str, Label] = {}
        for f in facts:
            if f.anchor in out:
                continue
            leaf = (f.path or f.anchor).split(".")[-1].split("[")[0].lower()
            name = self._name(f)
            sal = self._salience(f, leaf)
            out[f.anchor] = Label(name=name, salience=sal, group=group_of(f.anchor),
                                  reason="heuristic")
        return out

    @staticmethod
    def _name(f: Fact) -> str:
        if f.kind == FactKind.STATE:
            return f"{f.op} {f.resource}"
        if f.kind == FactKind.CONFIG:
            return f"config {f.anchor.split(':', 1)[-1]}"
        if f.kind == FactKind.PATH:
            return f"executed {f.method.split('.')[-1]}" if f.method else "executed"
        if f.kind == FactKind.FREQUENCY:
            return f"call count {f.anchor.split(':', 1)[-1]}"
        cls = f.method.split(".")[-2] if f.method and "." in f.method else (f.method or "")
        leaf = (f.path or "").replace("ret.", "").replace("ret", "result")
        return f"{cls}.{leaf}".strip(".") or f.anchor

    @staticmethod
    def _salience(f: Fact, leaf: str) -> Salience:
        if any(t in leaf for t in _LOW):
            return Salience.LOW
        if any(t in leaf for t in _HIGH):
            return Salience.HIGH
        if f.kind == FactKind.STATE:
            return Salience.HIGH       # a write/read at an external boundary IS the behavior
        if f.kind == FactKind.CONFIG:
            return Salience.HIGH       # config drift is the cheapest, highest-value catch
        if f.kind == FactKind.FREQUENCY:
            return Salience.MEDIUM
        return Salience.MEDIUM


# --------------------------------------------------------------------------- claude (optional)
class ClaudeLabeler:
    """Real meaning engine. Sends anchors + sample values (NOT used as ground truth) to Claude
    and asks for name/salience/group only. Falls back to the stub on any error so the pipeline
    never blocks on the network. Output is meant to be frozen onto the ledger and reused — label
    once, not every run (see ledger versioning)."""

    def __init__(self, model: str = "claude-opus-4-8", client=None):
        self.model = model
        self._client = client
        self._stub = StubLabeler()

    def label(self, facts: list[Fact]) -> dict[str, Label]:
        try:
            client = self._client or self._make_client()
        except Exception:
            return self._stub.label(facts)
        try:
            return self._label_via_llm(client, facts)
        except Exception:
            return self._stub.label(facts)

    def _make_client(self):
        import anthropic  # optional dependency; guarded
        return anthropic.Anthropic()

    def _label_via_llm(self, client, facts: list[Fact]) -> dict[str, Label]:
        import json
        # Group dedup: one ask per family keeps cost down and labels consistent.
        rep: dict[str, Fact] = {}
        for f in facts:
            rep.setdefault(group_of(f.anchor), f)
        catalog = [{
            "group": g,
            "anchor": f.anchor,
            "sample_value": _redacted_sample(f.value),
            "kind": f.kind.value,
        } for g, f in rep.items()]

        prompt = (
            "You assign MEANING to observed software behaviors. For each item return a name, a "
            "salience (high|medium|low: high = revenue/correctness-critical), and keep the given "
            "group. You are NOT judging whether any value is correct and NOT comparing values — "
            "only naming and ranking importance. Return JSON: "
            '{"labels":[{"group","name","salience","reason"}]}.\n\n'
            + json.dumps(catalog, default=str)
        )
        msg = client.messages.create(
            model=self.model, max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
        by_group: dict[str, Label] = {}
        for item in data.get("labels", []):
            try:
                by_group[item["group"]] = Label(
                    name=item["name"], salience=Salience(item["salience"]),
                    group=item["group"], reason=item.get("reason", "llm"))
            except Exception:
                continue
        # fan the per-group label back out to every anchor in that group; stub-fill any gaps
        out: dict[str, Label] = {}
        stub = self._stub.label(facts)
        for f in facts:
            lbl = by_group.get(group_of(f.anchor))
            out[f.anchor] = lbl or stub[f.anchor]
        return out


def _redacted_sample(value):
    """Never send a raw value that looks like PII to the meaning engine; the value is not the
    point of labeling anyway — only its shape/name is."""
    s = str(value)
    if "redacted" in s.lower():
        return "<redacted>"
    return value if len(s) <= 64 else s[:64] + "…"
