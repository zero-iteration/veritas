"""Coupling through shared state — the structure method-anchored capture cannot express.

Two observations do not connect to each other directly; they connect through a shared **anchor**.
When anchors are code symbols (`method#ret.price`), a write to `redis:price:SKU-42` in one service
and a read of it in another are two unrelated methods — the coupling is invisible. When the anchor
is the **external resource name**, both sides emit STATE facts on the same `resource`, and coupling
becomes a literal join: writers are upstream, readers are downstream, direction recovered from
read/write polarity (not guessed).

This is descriptive, not predictive: it reports what is observably coupled (and therefore what the
blast surface of perturbing a resource *is*), learned from one run's effects. No causal inference.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, asdict

from .facts import Fact, FactKind
from .label import group_of


@dataclass
class Coupling:
    resource: str                       # the shared external resource (the mediating node)
    family: str                         # key-family rollup (redis:price:SKU-{n})
    writers: list[str] = field(default_factory=list)  # methods that wrote it (upstream)
    readers: list[str] = field(default_factory=list)  # methods that read it (downstream)
    write_value: object = None
    read_value: object = None

    @property
    def is_coupling(self) -> bool:
        """A true coupling edge exists only when something writes AND something reads the resource
        — that is when perturbing the writer can reach a reader."""
        return bool(self.writers) and bool(self.readers)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CouplingEdge:
    writer: str
    reader: str
    resource: str

    def to_dict(self) -> dict:
        return asdict(self)


def _state_facts(facts: list[Fact]) -> list[Fact]:
    return [f for f in facts if f.kind == FactKind.STATE and f.resource]


def couplings(facts: list[Fact]) -> list[Coupling]:
    """Per-resource view: who writes it, who reads it, and the observed values on each side."""
    by_res: dict[str, dict] = defaultdict(lambda: {"w": [], "r": [], "wv": None, "rv": None})
    for f in _state_facts(facts):
        slot = by_res[f.resource]
        if f.op == "write":
            if f.method and f.method not in slot["w"]:
                slot["w"].append(f.method)
            slot["wv"] = f.value
        elif f.op == "read":
            if f.method and f.method not in slot["r"]:
                slot["r"].append(f.method)
            slot["rv"] = f.value
    out = []
    for res, slot in sorted(by_res.items()):
        out.append(Coupling(resource=res, family=group_of(f"res:{res}"),
                            writers=sorted(slot["w"]), readers=sorted(slot["r"]),
                            write_value=slot["wv"], read_value=slot["rv"]))
    return out


def coupling_edges(facts: list[Fact]) -> list[CouplingEdge]:
    """Directed writer->reader edges through each shared resource. This is the bipartite coupling
    promoted to an observation-to-observation graph."""
    edges = []
    for c in couplings(facts):
        for w in c.writers:
            for r in c.readers:
                if w != r:
                    edges.append(CouplingEdge(writer=w, reader=r, resource=c.resource))
    return edges


def blast_surface(facts: list[Fact], resource: str) -> list[str]:
    """If this resource's value moves, which readers are observably downstream? The descriptive
    blast radius — what a regression at `resource` can reach."""
    for c in couplings(facts):
        if c.resource == resource:
            return c.readers
    return []
