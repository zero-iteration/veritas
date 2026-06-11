"""Code-anchor freshness — expectations self-expire when the code they constrain changes.

resolve(): find a symbol's source span (Java/Python) and hash it.
is_stale(): re-resolve and compare to the stored hash. Anchor moved/edited -> stale,
so a verdict never asserts over code that no longer exists as claimed.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Optional

from .models import CodeAnchor


def _hash(span: str) -> str:
    # whitespace-normalized so reformatting alone doesn't churn the anchor
    norm = re.sub(r"\s+", " ", span).strip()
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def _find_method_span(text: str, method: str) -> Optional[tuple[int, str]]:
    """Find `method(` declaration and return (line_no, brace-matched body span)."""
    m = re.search(rf"(?<![\w.]){re.escape(method)}\s*\(", text)
    if not m:
        return None
    line_no = text.count("\n", 0, m.start()) + 1
    # extend to the opening brace, then brace-match
    brace = text.find("{", m.end())
    if brace == -1:
        # e.g. Python def: take the def line through indentation block
        eol = text.find("\n", m.start())
        return line_no, text[m.start(): eol if eol != -1 else len(text)]
    depth, i = 0, brace
    while i < len(text):
        if text[i] == "{": depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return line_no, text[m.start(): i + 1]
        i += 1
    return line_no, text[m.start():]


def resolve(symbol: str, roots: list[str]) -> Optional[CodeAnchor]:
    """symbol = 'pkg.sub.Class.method' (Java) or 'module.Class.method' / 'func' (Python)."""
    parts = symbol.split(".")
    method = parts[-1]
    simple_class = parts[-2] if len(parts) >= 2 else None
    exts = (".java", ".py", ".kt", ".ts", ".js")
    for root in roots:
        for dirpath, _, files in os.walk(root):
            if any(skip in dirpath for skip in (".git", "node_modules", "target", "__pycache__", ".venv")):
                continue
            for fn in files:
                if not fn.endswith(exts):
                    continue
                if simple_class and fn.rsplit(".", 1)[0] != simple_class:
                    # for Python a class can share a file; only hard-filter for Java-like
                    if fn.endswith((".java", ".kt")):
                        continue
                path = os.path.join(dirpath, fn)
                try:
                    text = open(path, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                if simple_class and (f"class {simple_class}" not in text and f"interface {simple_class}" not in text):
                    continue
                span = _find_method_span(text, method)
                if span:
                    line_no, body = span
                    return CodeAnchor(symbol=symbol, file=path, line=line_no, anchor_hash=_hash(body))
    return None


def is_stale(anchor: CodeAnchor, roots: list[str]) -> bool:
    """True if the anchored code no longer matches the stored hash (or vanished)."""
    if anchor.anchor_hash is None:
        return False
    fresh = resolve(anchor.symbol, roots)
    return fresh is None or fresh.anchor_hash != anchor.anchor_hash
