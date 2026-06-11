# Veritas — the divergence engine

**An agent proposes a diagnosis or a fix; Veritas runs the real code path and returns a
verdict — confirmed or contradicted, with the actual numbers — instead of more context.**

Everyone else moves *data* to the agent (logs, traces, telemetry) and makes it find the
contradiction inside a 50k-token window. Veritas moves a *conclusion*: "you expected X, the
runtime did Y, here are the two values that disagree" — ~300 tokens — and targets the #1 agent
failure mode (wrong / symptom-level patches), not localization.

```
VERDICT: CONTRADICTED  (exp_0001, observed under staging, 2026-06-11, sha 0fed1b1)
  expected : cheapest rate wins at pick
  observed : chose {carrier=STANDARD, price=549} (price=549) over {carrier=PARTNER, price=519} (price=519)  ← cheaper, lost
  mechanism: pick selects via argmin(price); returned price=549 while a candidate had 519 — selection isn't governed by price
  config   : rate.selection.strategy: file='cheapest_selection' but RUNTIME='margin_max_v3'  [trust observed]
  evidence : trace run_0617 (env staging), 3 captured invocation(s)
  confidence: HIGH
```

## Quickstart

```bash
./run-demo.sh        # builds the JVM agent, runs an instrumented demo, prints a real verdict
python3 -m veritas.cli list
```

Requires JDK 8/11/17 + Maven (capture agent) and Python ≥3.9 (join engine — zero runtime deps).

## Architecture

```
CAPTURE (veritas-agent, ByteBuddy)        ORACLE (.veritas/expectations.json)
  methods + edges + decision VALUES         code-anchored, self-invalidating claims
  + live config getters                            │
        │ trace.json (the contract)                │
        └──────────────┬───────────────────────────┘
                ┌──────▼─────────────────────────┐
                │ JOIN ENGINE  (expectation ×     │   ← the IP
                │ observation → Verdict + config/ │
                │ path divergence + uncertainty)  │
                └──────┬─────────────────────────┘
                ┌──────▼─────────────────────────┐
                │ INTERFACE: MCP server + CLI     │
                │ verify / drive / explain /      │
                │ observed-config / diff          │
                └─────────────────────────────────┘
```

- **Capture** (`veritas-agent/`, Java): deterministic instrumentation — no sampling floor.
  Deep value extraction unfolds args/returns to decision fields (primitives/boxed/String/enum),
  bounded; config getters captured as `(key) → live value`; private/leaf methods included.
  `premain` and `attach`.
- **Join** (`veritas/`, Python): the predicate is evaluated on captured values →
  `CONFIRMED` / `CONTRADICTED` / `UNVERIFIABLE`. UNVERIFIABLE is never a guess — it's the
  honesty valve, and it says exactly what to run to close the gap. Two ambient divergences
  fire on *every* join regardless of the predicate: **config** (file vs live) and **path**
  (anchors that never executed).
- **Oracle** (`.veritas/`): expectations are code-anchored and freshness-hashed, so they
  self-expire when the code they constrain changes. This corpus is the accreting moat.

## The five tools (MCP + CLI)

| tool | job |
|---|---|
| `veritas_verify(claim, anchor, predicate, env)` | the headline — verdict with values |
| `veritas_drive(scenario, env)` | draft a human-in-the-loop reproduction so the path executes |
| `veritas_explain(symbol)` | ACTUAL / EXPECTED / DIVERGENCE join for a symbol |
| `veritas_observed_config(glob, env)` | file-vs-live config guard (ambient, cheap, daily) |
| `veritas_diff(before, after)` | behavior diff of a fix under prod-true config |

MCP: `python -m veritas.mcp_server` (stdio JSON-RPC; set `VERITAS_ROOT`).

## Predicate forms (agent-authored, human-readable, machine-checkable)

```
relationship : {over, select: argmin|argmax, by, equals: ret|ret.<f>}   # the returned element must be the selected one
value        : {field: ret.<f>|arg.<n>.<f>, op, value}                  # a captured field == / in-range a literal
config       : {key, op, value}                                         # a live config key equals an expected value
path         : {method, must: executed|not_executed}                    # was it actually executed
```

## Status

**v0.2** (shipped): end-to-end on real capture, plus the capture-hardening that unblocks
real services —
- **expectation-driven nested field unfolding** (`unfold=breakdown.netPrice` captures fields
  two POJOs deep, depth-exempt) — derive the attach args with `veritas capture-args`;
- **PII redaction** at write time (field-name denylist + value-shape redactors);
- **class-qualified** `captureValues` (`Class.method`), **periodic flush** (crash-safe);
- engine: **dotted by-paths**, **numeric coercion** guard, **accretive observation merge**.

v0.1 base: deep values + config-getter keying + private methods; CLI + MCP
(`verify`/`explain`/`observed-config`/`diff`/`drive`/`capture-args`); self-invalidating
anchors; behavior locked by 10 tests.

Roadmap: the **drive layer** (endpoint discovery + reproduction synthesis + write-safety
classifier + scenario replay) — the supply side, currently a HITL scaffold; session-start
**push hook** (inject open contradictions + config divergences); grow the **GroundedDebug**
benchmark (`docs/grounded-debug.md`) to 10+ bugs for the flip-rate number. JVM/Spring first.
