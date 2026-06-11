"""Veritas MCP server — the five agent tools over stdio JSON-RPC 2.0.

Self-contained (no SDK dependency): newline-delimited JSON-RPC, implementing
initialize / tools/list / tools/call. Point an MCP client (Claude Code / Cursor) at:
    command: python   args: ["-m", "veritas.mcp_server"]   env: {VERITAS_ROOT: "/path/to/repo"}
"""
from __future__ import annotations

import json
import os
import sys

from .workspace import Workspace
from .render import render_verdict


TOOLS = [
    {
        "name": "veritas_verify",
        "description": "Before committing to a root cause or fix: is the claim actually true on the "
                       "running system? Returns a verdict (CONFIRMED/CONTRADICTED/UNVERIFIABLE) with the "
                       "real captured values — not more context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "claim": {"type": "string", "description": "natural-language falsifiable claim"},
                "kind": {"type": "string", "enum": ["relationship", "value", "config", "path"]},
                "anchor": {"type": "string", "description": "code anchor, e.g. com.example.RateSelector.pick"},
                "predicate": {"type": "object", "description": "machine-checkable predicate (see docs)"},
                "expectation_id": {"type": "string", "description": "verify an existing expectation instead"},
                "env": {"type": "string"},
            },
        },
    },
    {
        "name": "veritas_drive",
        "description": "Draft a human-in-the-loop reproduction request for a scenario/ticket so the path "
                       "actually executes and can be captured.",
        "inputSchema": {"type": "object", "properties": {
            "scenario": {"type": "string"}, "env": {"type": "string"}}, "required": ["scenario"]},
    },
    {
        "name": "veritas_explain",
        "description": "ACTUAL/EXPECTED/DIVERGENCE join for a symbol: what really executed, captured values, "
                       "open expectations, and config/path divergences.",
        "inputSchema": {"type": "object", "properties": {
            "symbol": {"type": "string"}, "env": {"type": "string"}}, "required": ["symbol"]},
    },
    {
        "name": "veritas_observed_config",
        "description": "File-declared vs observed-LIVE config for a key glob. Catches the whole "
                       "confident-wrong-from-stale-config bug class.",
        "inputSchema": {"type": "object", "properties": {
            "key_glob": {"type": "string"}, "env": {"type": "string"}}, "required": ["key_glob"]},
    },
    {
        "name": "veritas_diff",
        "description": "Behavior diff of a fix: value + path changes between two observations "
                       "(before vs after, under prod-true config).",
        "inputSchema": {"type": "object", "properties": {
            "before": {"type": "string"}, "after": {"type": "string"}}, "required": ["before", "after"]},
    },
]


def _result(rid, result): return {"jsonrpc": "2.0", "id": rid, "result": result}
def _error(rid, code, msg): return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}


def call_tool(name: str, args: dict, root: str) -> str:
    ws = Workspace(root)
    if name == "veritas_verify":
        if args.get("expectation_id"):
            v = ws.verify(args["expectation_id"], args.get("env"))
        else:
            v = ws.verify_claim(args["claim"], args.get("kind", "relationship"),
                                args["anchor"], args["predicate"], env=args.get("env"))
        return render_verdict(v)
    if name == "veritas_drive":
        return json.dumps(ws.drive(args["scenario"], args.get("env", "staging")), indent=2)
    if name == "veritas_explain":
        return json.dumps(ws.explain(args["symbol"], args.get("env")), indent=2, default=str)
    if name == "veritas_observed_config":
        return json.dumps(ws.observed_config(args["key_glob"], args.get("env")), indent=2, default=str)
    if name == "veritas_diff":
        return json.dumps(ws.diff(args["before"], args["after"]), indent=2, default=str)
    return f"unknown tool: {name}"


def handle(req: dict, root: str):
    method, rid = req.get("method"), req.get("id")
    if method == "initialize":
        return _result(rid, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                             "serverInfo": {"name": "veritas", "version": "0.1.0"}})
    if method == "tools/list":
        return _result(rid, {"tools": TOOLS})
    if method == "tools/call":
        p = req.get("params", {})
        try:
            text = call_tool(p["name"], p.get("arguments", {}), root)
            return _result(rid, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as e:  # surface as a tool error, never crash the server
            return _result(rid, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(rid, {})
    return _error(rid, -32601, f"method not found: {method}")


def main():
    root = os.environ.get("VERITAS_ROOT", ".")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(req, root)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
