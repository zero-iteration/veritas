# GroundedDebug — the benchmark (category scoreboard)

The number nobody else has: **how often an execution verdict flips an agent's diagnosis on
config/value-dependent bugs** — and how much it cuts the wrong-fix rate. Measured on diagnosis
*correctness*, not localization (the crowded 8.7% metric).

## Design

- **Cases** (`benchmark/bugs/*.json`): real config/value-dependent bugs harvested prospectively,
  each with a captured trace, a falsifiable expectation, and ground truth (the real cause vs the
  tempting wrong symptom). Seeded config divergences are legitimate — the bug is real and the env
  reproduces it.
- **Two arms, model held constant**: `agent + grep` vs `agent + veritas_verify`.
- **Metrics**:
  - **flip-rate** (headline) — fraction of diagnoses changed by a verdict.
  - **wrong-fix rate** — fraction landing on the symptom-level patch, per arm.
  - **verifiable-rate** — fraction where Veritas returned a verdict (not UNVERIFIABLE). The honest
    coverage number; reported alongside precision so a high-precision tool can't hide behind
    constant UNVERIFIABLE.
  - **verdict-precision** — fraction of CONTRADICTED that are true contradictions. Target ~100%;
    one false contradiction kills trust (UNVERIFIABLE is the pressure valve).

## Running

```bash
python3 benchmark/bench.py                       # veritas side: verifiable-rate + precision
python3 benchmark/bench.py --arm-alone alone.json --arm-veritas veritas.json   # + flip / wrong-fix
```

Arm files map `bug_id -> "real_cause" | "wrong_symptom"`, recorded from held-constant agent runs.

## Honesty guardrails (pre-registered)

- Pre-register the bug-class definition before harvesting cases.
- Include OSS repos, not just one codebase, to defuse home-turf-bias.
- Publish negative cases where the verdict did **not** help.
