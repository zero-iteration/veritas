#!/usr/bin/env python3
"""GroundedDebug harness — the category scoreboard.

Each bug case (benchmark/bugs/*.json) is a real config/value-dependent bug with a
captured trace + a falsifiable expectation + ground truth. Two arms, model held
constant: `agent+grep` vs `agent+veritas_verify`. Headline metric is the
DIAGNOSIS-FLIP rate (not localization) and the WRONG-FIX rate.

This runs the veritas side directly (verifiable-rate + verdict precision). Flip-rate
and wrong-fix-rate are computed when two arm result files are supplied (recorded from
held-constant agent runs):  --arm-alone alone.json --arm-veritas veritas.json
  arm file shape:  {"bug_0001": "wrong_symptom" | "real_cause", ...}
"""
import argparse
import glob
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from veritas.workspace import Workspace
from veritas.models import VerdictType


def load_bugs(d):
    return [json.load(open(p)) for p in sorted(glob.glob(os.path.join(d, "bugs", "*.json")))]


def run_veritas(bug, repo_root):
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(tmp)
        ws.ingest(os.path.join(repo_root, bug["trace"]),
                  os.path.join(repo_root, bug["config"]) if bug.get("config") else None,
                  env=bug.get("env"))
        v = ws.verify_claim(bug["claim"], bug["kind"], bug["anchor"], bug["predicate"], env=bug.get("env"))
        return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--arm-alone")
    ap.add_argument("--arm-veritas")
    a = ap.parse_args()

    bugs = load_bugs(a.dir)
    verifiable = precise = 0
    print(f"GroundedDebug: {len(bugs)} bug case(s)\n")
    for bug in bugs:
        v = run_veritas(bug, a.repo)
        is_verifiable = v.type in (VerdictType.CONFIRMED, VerdictType.CONTRADICTED)
        is_precise = v.type.value == bug["ground_truth"]["verdict"]
        verifiable += is_verifiable
        precise += is_precise
        print(f"  {bug['id']}: veritas={v.type.value:13s} expected={bug['ground_truth']['verdict']:13s} "
              f"{'OK' if is_precise else 'MISS'}")

    n = len(bugs)
    print(f"\n  verifiable-rate : {verifiable}/{n} = {verifiable/n:.0%}   (rest are honest UNVERIFIABLE)")
    print(f"  verdict-precision: {precise}/{verifiable if verifiable else 1} "
          f"= {precise/(verifiable or 1):.0%}   (target ~100% — one false contradiction kills trust)")

    if a.arm_alone and a.arm_veritas:
        alone = json.load(open(a.arm_alone)); ver = json.load(open(a.arm_veritas))
        flips = wrong_alone = wrong_ver = 0
        for bug in bugs:
            bid = bug["id"]
            da, dv = alone.get(bid), ver.get(bid)
            if da != dv:
                flips += 1
            wrong_alone += (da == "wrong_symptom")
            wrong_ver += (dv == "wrong_symptom")
        print(f"\n  FLIP-RATE (headline): {flips}/{n} = {flips/n:.0%}  diagnoses changed by a verdict")
        print(f"  wrong-fix rate: agent-alone {wrong_alone/n:.0%}  ->  agent+veritas {wrong_ver/n:.0%}")
    else:
        print("\n  (supply --arm-alone / --arm-veritas recorded diagnoses for flip-rate + wrong-fix-rate)")


if __name__ == "__main__":
    main()
