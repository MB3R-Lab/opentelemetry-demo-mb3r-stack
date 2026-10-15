# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
"""Explicit manual exploration; never the required PR check."""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import re
import sys

import gate
import demo_report

WORKFLOW_NAME = "MB3R parameterized demo"
NUMBERS = {
    "slo": (0, 1), "failure_probability": (0, 1),
    "edge_live_probability": (0, 1),
    "minimum_certified_tolerance": (0, 1), "max_regression": (0, 1),
    "trials": (1000, 100000), "seed": (0, 2147483647),
}
INPUTS = {"baseline_ref", "baseline_replicas", "candidate_replicas", *NUMBERS}


def require_manual():
    if (os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_WORKFLOW") != WORKFLOW_NAME):
        raise ValueError("Overrides are only available in the dedicated manual workflow")


def parse_replicas(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-4]/[1-4]", value):
        raise ValueError("Replicas must be recommendation/catalog, each 1..4 (for example 2/1)")
    recommendation, catalog = map(int, value.split("/"))
    return gate.replicas({"replicas": {
        "recommendation": recommendation, "product-catalog": catalog}})


def configuration(inputs, base_policy):
    if not isinstance(inputs, dict) or set(inputs) != INPUTS:
        raise ValueError("Missing or unknown manual inputs")
    if not isinstance(inputs["baseline_ref"], str) or not inputs["baseline_ref"].strip():
        raise ValueError("baseline_ref is required")
    policy = copy.deepcopy(base_policy)
    for key, (minimum, maximum) in NUMBERS.items():
        raw = inputs[key]
        if isinstance(raw, bool):
            raise ValueError(f"{key} must be a number, not a boolean")
        value = float(raw)
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{key} must be finite and in [{minimum}, {maximum}]")
        if key in ("seed", "trials"):
            if not value.is_integer():
                raise ValueError(f"{key} must be an integer")
            value = int(value)
        policy[key] = value
    # The grid and operation contract stay versioned. No interpolation is invented.
    if not 0 < policy["slo"] <= 1:
        raise ValueError("SLO must be greater than zero")
    if policy["minimum_certified_tolerance"] > max(policy["grid"]):
        raise ValueError("Minimum tolerance exceeds the versioned sweep grid")
    return {
        "mode": "manual-exploration-not-release-approval",
        "inputs": inputs, "policy": policy,
        "replicas": {phase: parse_replicas(inputs[f"{phase}_replicas"])
                     for phase in ("baseline", "candidate")},
    }


def configure():
    require_manual()
    value = configuration(json.loads(os.environ["DEMO_INPUTS_JSON"]), gate.policy())
    gate.write(gate.OUT / "manual-run.json", value)


def execute(action, phase):
    require_manual()
    config = gate.read(gate.OUT / "manual-run.json")
    kwargs = {"policy_value": config["policy"]}
    if action == "prepare":
        kwargs["replica_override"] = config["replicas"][phase]
    if action == "analyze":
        # Exploratory baselines may fail a user-selected SLO. Still show both
        # calculations; final enforcement will keep the run red.
        kwargs["allow_rejected_baseline"] = True
    getattr(gate, action)(phase, **kwargs)


def report():
    require_manual()
    rows, errors = [], []
    config = {}
    try:
        config = gate.read(gate.OUT / "manual-run.json")
    except (OSError, ValueError) as error:
        errors.append(f"No valid manual configuration: {error}")
    for phase in ("baseline", "candidate"):
        try:
            row = gate.report_row(phase)
            raw = gate.read(gate.OUT / phase / "sheaft/report.json")
            gate.check_exit(row["code"], raw)
            sweep = next(s for s in raw["sweeps"] if s["name"] == "operation-boundary")
            row["sweep"] = sweep
            rows.append(row)
        except (OSError, ValueError, KeyError, StopIteration) as error:
            errors.append(f"{phase}: incomplete evidence ({error})")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_url = f"https://github.com/{repository}/actions/runs/{run_id}"
    result = {
        "mode": "manual-exploration-not-release-approval",
        "run_url": run_url, "run_id": run_id,
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "workflow_commit": os.environ.get("GITHUB_SHA", ""),
        "endpoint": gate.ENDPOINT, "configuration": config,
        "rows": rows, "errors": errors,
    }
    gate.write(gate.OUT / "result.json", result)
    # Publish only the small derived report, never raw responses or logs to Pages.
    site = gate.OUT / "site"
    gate.write(site / "result.json", result)
    (site / "index.html").write_text(demo_report.render(result), encoding="utf-8")
    summary = demo_report.summary(result)
    (gate.OUT / "summary.md").write_text(summary, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["configure", "prepare", "observe", "discover",
                                         "analyze", "report", "enforce"])
    parser.add_argument("phase", nargs="?", choices=["baseline", "candidate"])
    args = parser.parse_args()
    require_manual()
    gate.OUT = gate.ROOT / "mb3r/out/demo"
    if args.action in ("configure", "report"):
        globals()[args.action]()
    elif args.action == "enforce":
        return gate.enforce()
    else:
        if not args.phase:
            parser.error("phase is required")
        execute(args.action, args.phase)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, StopIteration) as error:
        print(f"Manual demo evidence/configuration error: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
