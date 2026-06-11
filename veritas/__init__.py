"""Veritas — the divergence engine. An agent proposes a diagnosis or a fix; Veritas
runs the real code path and returns a verdict (confirmed / contradicted / unverifiable)
with the actual numbers, instead of more context."""
from .models import (
    Expectation, Observation, Verdict, Predicate, CodeAnchor, Invocation, EnvFingerprint,
    Kind, Status, Grade, VerdictType, Confidence, Source, Divergence,
)
from .workspace import Workspace
from .join import join
from .render import render_verdict

__version__ = "0.1.0"
__all__ = [
    "Expectation", "Observation", "Verdict", "Predicate", "CodeAnchor", "Invocation",
    "EnvFingerprint", "Kind", "Status", "Grade", "VerdictType", "Confidence", "Source",
    "Divergence", "Workspace", "join", "render_verdict",
]
