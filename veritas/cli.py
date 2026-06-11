"""veritas CLI — mirrors the five agent tools: verify / drive / explain / observed-config / diff
(plus plumbing: expect, ingest, endpoints, list)."""
from __future__ import annotations

import argparse
import json
import sys

from .workspace import Workspace
from .render import render_verdict


def _ws(args) -> Workspace:
    return Workspace(getattr(args, "root", ".") or ".")


def cmd_expect(a):
    ws = _ws(a)
    pred = json.loads(a.predicate)
    exp = ws.create_expectation(a.claim, a.kind, a.anchor, pred, source=a.source, ticket=a.ticket)
    print(f"created {exp.id}  [{exp.kind.value}, grade={exp.grade.value}, anchor={exp.anchor.symbol}"
          f"{' @'+exp.anchor.file if exp.anchor.file else ' (unresolved)'}]")
    if a.verify:
        print(); print(render_verdict(ws.verify(exp.id, a.env)))


def cmd_verify(a):
    ws = _ws(a)
    if a.claim:
        v = ws.verify_claim(a.claim, a.kind, a.anchor, json.loads(a.predicate), env=a.env, source=a.source)
    else:
        v = ws.verify(a.expectation, a.env)
    print(render_verdict(v))


def cmd_drive(a):
    print(json.dumps(_ws(a).drive(a.scenario, a.env), indent=2))


def cmd_explain(a):
    print(json.dumps(_ws(a).explain(a.symbol, a.env), indent=2, default=str))


def cmd_observed_config(a):
    print(json.dumps(_ws(a).observed_config(a.glob, a.env), indent=2, default=str))


def cmd_diff(a):
    print(json.dumps(_ws(a).diff(a.before, a.after), indent=2, default=str))


def cmd_ingest(a):
    obs = _ws(a).ingest(a.trace, a.config, a.env)
    print(f"ingested {obs.id}  env={obs.fingerprint.env}  methods={len(obs.methods_executed)}  "
          f"invocations={len(obs.invocations)}  config_live={len(obs.config_live)}")


def cmd_capture_args(a):
    print(json.dumps(_ws(a).capture_args(), indent=2))


def cmd_list(a):
    ws = _ws(a)
    for e in ws.exp.all():
        print(f"  {e.id}  [{e.status.value}/{e.grade.value}]  {e.claim}")
    print(f"  -- {len(ws.obs.all())} observation(s)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("veritas", description="execution verdicts for AI coding agents")
    p.add_argument("--root", default=".", help="workspace root (.veritas lives here)")
    s = p.add_subparsers(dest="cmd", required=True)

    pe = s.add_parser("expect", help="register an expectation")
    pe.add_argument("--claim", required=True); pe.add_argument("--kind", required=True,
                    choices=["relationship", "value", "config", "path"])
    pe.add_argument("--anchor", required=True); pe.add_argument("--predicate", required=True)
    pe.add_argument("--source", default="agent"); pe.add_argument("--ticket")
    pe.add_argument("--verify", action="store_true"); pe.add_argument("--env")
    pe.set_defaults(fn=cmd_expect)

    pv = s.add_parser("verify", help="verdict for an expectation (or an ad-hoc claim)")
    pv.add_argument("expectation", nargs="?"); pv.add_argument("--env")
    pv.add_argument("--claim"); pv.add_argument("--kind", default="relationship")
    pv.add_argument("--anchor"); pv.add_argument("--predicate"); pv.add_argument("--source", default="agent")
    pv.set_defaults(fn=cmd_verify)

    pd = s.add_parser("drive", help="draft a HITL reproduction request")
    pd.add_argument("scenario"); pd.add_argument("--env", default="staging"); pd.set_defaults(fn=cmd_drive)

    px = s.add_parser("explain", help="ACTUAL/EXPECTED/DIVERGENCE for a symbol")
    px.add_argument("symbol"); px.add_argument("--env"); px.set_defaults(fn=cmd_explain)

    pc = s.add_parser("observed-config", help="file-vs-live config for a key glob")
    pc.add_argument("glob"); pc.add_argument("--env"); pc.set_defaults(fn=cmd_observed_config)

    pf = s.add_parser("diff", help="behavior diff of two observations (fix verification)")
    pf.add_argument("before"); pf.add_argument("after"); pf.set_defaults(fn=cmd_diff)

    pi = s.add_parser("ingest", help="ingest a capture-agent trace")
    pi.add_argument("trace"); pi.add_argument("--config"); pi.add_argument("--env"); pi.set_defaults(fn=cmd_ingest)

    pca = s.add_parser("capture-args", help="derive agent attach args (scope/captureValues/unfold) from expectations")
    pca.set_defaults(fn=cmd_capture_args)

    pl = s.add_parser("list", help="list expectations + observations"); pl.set_defaults(fn=cmd_list)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    main()
