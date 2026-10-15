# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
"""Application-side CLI orchestration, evidence capture and reporting; no simulator."""
import argparse
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "mb3r/out/ci"
SERVICES = {"recommendation", "product-catalog"}
ENDPOINT = "frontend:GET /api/recommendations"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(args, *, capture=False, allowed=(0,), env=None):
    print("+", " ".join(str(x) for x in args), flush=True)
    result = subprocess.run([str(x) for x in args], cwd=ROOT, env=env,
                            text=True, encoding="utf-8", capture_output=capture)
    if result.returncode not in allowed:
        raise RuntimeError(f"exit {result.returncode}: {args}\n"
                           f"{result.stdout or ''}\n{result.stderr or ''}")
    return result


def base_source():
    return Path(os.environ["BASE_SOURCE"]).resolve()


def policy():
    value = read(base_source() / "mb3r/ci/policy.json")
    if value["endpoint"] != ENDPOINT or value["request_path"] != "/api/recommendations?currencyCode=USD":
        raise ValueError("This integration checks the declared nonempty USD operation only")
    return value


def replicas(value):
    counts = value["replicas"]
    if set(counts) != SERVICES:
        raise ValueError("Replica map must contain exactly recommendation and product-catalog")
    if any(type(n) is not int or not 1 <= n <= 4 for n in counts.values()):
        raise ValueError("CI replica counts must be integers from 1 to 4")
    return counts


def context(phase):
    source = base_source() if phase == "baseline" else ROOT
    profile = source / "mb3r/ci"
    work = OUT / phase
    revision = run(["git", "-C", source, "rev-parse", "HEAD"], capture=True).stdout.strip()
    env = dict(os.environ, APP_SOURCE=str(source), APP_REVISION=revision,
               CI_PROFILE=str(profile), COMPOSE_PROJECT_NAME=f"mb3r-ci-{phase}")
    command = ["docker", "compose", "-f", profile / "compose.yaml",
               "-f", work / "replicas.json"]
    return source, profile, work, revision, env, command


def prepare(phase, *, replica_override=None, policy_value=None):
    source, profile, work, revision, env, command = context(phase)
    deployment = read(profile / "deployment.json")
    if replica_override is not None:
        deployment = {"description": "Manual exploratory override", "replicas": replica_override}
    counts = replicas(deployment)
    effective_policy = policy() if policy_value is None else policy_value
    write(work / "effective-deployment.json", deployment)
    write(work / "effective-policy.json", effective_policy)
    write(work / "replicas.json",
          {"services": {s: {"deploy": {"replicas": n}} for s, n in counts.items()}})
    resolved = json.loads(run(command + ["config", "--format", "json"],
                              capture=True, env=env).stdout)
    for service, count in counts.items():
        if resolved["services"][service]["deploy"]["replicas"] != count:
            raise ValueError("Deployment replica declaration was not applied")
    write(work / "static/compose.yaml", resolved)
    # The operation definition and policy come from the base commit, not the PR.
    shutil.copy2(base_source() / "mb3r/ci/recommendations.tracetest.yaml",
                 work / "static/recommendations.tracetest.yaml")
    write(work / "provenance.json", {
        "phase": phase, "application_commit": revision,
        "workflow_run": os.environ.get("GITHUB_RUN_ID"),
        "workflow_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "toolchain_image": os.environ.get("TOOLCHAIN_IMAGE"),
        "replicas": counts, "deployment_sha256": sha256(profile / "deployment.json"),
        "policy_sha256": sha256(base_source() / "mb3r/ci/policy.json"),
        "effective_deployment_sha256": sha256(work / "effective-deployment.json"),
        "effective_policy_sha256": sha256(work / "effective-policy.json"),
        "manual_replica_override": replica_override is not None,
        "resolved_compose_sha256": sha256(work / "static/compose.yaml"),
    })
    run(["procrustes", "analyze", "--source", f"execution={work / 'static'}",
         "--goal", base_source() / "mb3r/ci/goal.yaml",
         "--out-dir", work / "procrustes", "--fail-on-blocked"])
    assessment = read(work / "procrustes/report.json")
    if assessment.get("goal_status") != "EXPECTED":
        raise ValueError(f"Preflight is not EXPECTED: {assessment.get('goal_status')}")


def response_success(status, body):
    try:
        data = json.loads(body)
        return status == 200 and isinstance(data, list) and bool(data) and all(
            isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"].strip()
            for p in data)
    except (ValueError, TypeError):
        return False


def request(path):
    trace_id = secrets.token_hex(16)
    req = Request("http://127.0.0.1:8080" + path, headers={
        "traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01"})
    try:
        with urlopen(req, timeout=10) as response:
            status, body = response.status, response.read(1 << 20).decode()
    except HTTPError as error:
        status, body = error.code, error.read(1 << 20).decode()
    return {"trace_id": trace_id, "http_status": status, "body": body,
            "success": bool(response_success(status, body)),
            "observed_at": datetime.now(timezone.utc).isoformat()}


def normalize_trace(raw, trace_id):
    traces = raw.get("data", [])
    if len(traces) != 1 or traces[0].get("traceID") != trace_id:
        raise ValueError("Jaeger response does not match the fresh request trace ID")
    trace = traces[0]
    spans, seen = [], set()
    for span in trace["spans"]:
        if span["traceID"] != trace_id or span["spanID"] in seen:
            raise ValueError("Wrong trace ID or duplicated span")
        seen.add(span["spanID"])
        attrs = {t["key"]: t["value"] for t in span["tags"]}
        parent = next((r["spanID"] for r in span["references"]
                       if r["refType"] == "CHILD_OF"), "")
        stamp = lambda micros: datetime.fromtimestamp(
            micros / 1_000_000, timezone.utc).isoformat().replace("+00:00", "Z")
        spans.append({
            "trace_id": trace_id, "span_id": span["spanID"], "parent_span_id": parent,
            "service": trace["processes"][span["processID"]]["serviceName"],
            "name": span["operationName"], "kind": attrs.get("span.kind", "internal"),
            "attributes": attrs, "start_time": stamp(span["startTime"]),
            "end_time": stamp(span["startTime"] + span["duration"]),
        })
    if not {"frontend", *SERVICES}.issubset({s["service"] for s in spans}):
        raise ValueError("Incomplete operation trace")
    return spans


def collect_trace(trace_id):
    previous = None
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        time.sleep(2)
        try:
            with urlopen("http://127.0.0.1:16686/api/traces/" + trace_id, timeout=5) as response:
                raw = json.load(response)
            spans = normalize_trace(raw, trace_id)
            signature = sorted(s["span_id"] for s in spans)
            if signature == previous:
                return raw, spans
            previous = signature
        except (OSError, HTTPException, ValueError, KeyError):
            continue
    raise RuntimeError("No complete fresh operation trace; archival fallback is forbidden")


def containers(text):
    # Compose versions emit either a JSON array or newline-delimited objects.
    try:
        value = json.loads(text)
        return value if isinstance(value, list) else [value]
    except ValueError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def verify_running(rows, expected):
    for service, count in expected.items():
        actual = [r for r in rows if r.get("Service") == service and r.get("State") == "running"]
        if len(actual) != count:
            raise ValueError(f"{service}: {len(actual)} running replicas, expected {count}")


def wait_ready(path, work):
    deadline = time.monotonic() + 180
    last = {"error": "no response"}
    while time.monotonic() < deadline:
        try:
            last = request(path)
            if last["success"]:
                return
        except (OSError, HTTPException) as error:
            last = {"error": str(error)}
        time.sleep(2)
    write(work / "readiness-failure.json", last)
    raise RuntimeError("Nonempty USD operation did not become healthy")


def observe(phase, *, policy_value=None):
    _, _, work, _, env, command = context(phase)
    p = policy() if policy_value is None else policy_value
    try:
        run(command + ["build", "--parallel"], env=env)
        run(command + ["up", "-d"], env=env)
        wait_ready(p["request_path"], work)
        state = containers(run(command + ["ps", "--all", "--format", "json"],
                               capture=True, env=env).stdout)
        write(work / "containers.json", state)
        verify_running(state, read(work / "provenance.json")["replicas"])
        observations, spans = [], []
        for attempt in (1, 2):
            observed = request(p["request_path"])
            observations.append(observed)
            write(work / "observations.json", observations)
            if not observed["success"]:
                raise RuntimeError("Request failed; no valid model-based acceptance evidence")
            raw, normalized = collect_trace(observed["trace_id"])
            write(work / f"traces/request-{attempt}.json", raw)
            spans.extend(normalized)
        write(work / "bering-traces.json", {"spans": spans})
        metadata = read(work / "provenance.json")
        metadata.update(requests=2, spans=len(spans),
                        discovered_at=max(s["end_time"] for s in spans))
        write(work / "provenance.json", metadata)
    finally:
        work.mkdir(parents=True, exist_ok=True)
        for filename, args in [
            ("service-logs.txt", ["logs", "--no-color", "--tail", "80"]),
            ("images.json", ["images", "--format", "json"]),
        ]:
            result = run(command + args, capture=True, allowed=tuple(range(256)), env=env)
            (work / filename).write_text(result.stdout + result.stderr, encoding="utf-8")
        # Only the disposable, named CI Compose project and its anonymous volumes.
        run(command + ["down", "-v", "--remove-orphans"], env=env)


def bind_calls(model, pairs):
    required = []
    for source, target in pairs:
        found = [e["id"] for e in model["edges"]
                 if e["from"] == source and e["to"] == target
                 and e["kind"] == "sync" and e["blocking"] is True]
        if len(found) != 1:
            raise ValueError(f"Expected exactly one observed call {source}->{target}: {found}")
        required.extend(found)
    return sorted(required)


def discover(phase, *, policy_value=None):
    work = OUT / phase
    p = policy() if policy_value is None else policy_value
    meta = read(work / "provenance.json")
    trace = work / "bering-traces.json"
    preflight = work / "procrustes/bering.overlay.yaml"
    common = ["bering", "discover", "--input", trace, "--overlay", preflight,
              "--discovered-at", meta["discovered_at"]]
    run(common + ["--out", work / "observed-model.json"])
    observed = read(work / "observed-model.json")
    required = bind_calls(observed, p["required_call_pairs"])
    predicate = {"type": "edge_aware", "entry_service": "frontend",
                 "mandatory_targets": sorted(SERVICES), "edge_modes": ["sync"],
                 "required_edges": required}
    write(work / "predicate.json", {"schema_version": "1.1",
                                  "predicates": {p["predicate_ref"]: predicate}})
    overlay = {"name": "recommendations-ci", "services": [
        dict(id=s["id"], failure_eligible=s["id"] in SERVICES,
             **({"replicas": meta["replicas"][s["id"]]} if s["id"] in SERVICES else {}))
        for s in observed["services"]], "endpoints": [{
            "id": ENDPOINT, "entry_service": "frontend", "method": "GET",
            "path": "/api/recommendations", "predicate_ref": p["predicate_ref"],
            "semantics": {"predicate_mode": "external_predicate",
                          "mandatory_targets": sorted(SERVICES),
                          "dependency_modes": ["sync"], "source": "declared-ci-operation"},
        }]}
    write(work / "operation-overlay.json", overlay)
    run(common + ["--overlay", work / "operation-overlay.json",
                  "--out", work / "model.json", "--snapshot-out", work / "snapshot.json"])
    model = read(work / "model.json")
    if {e["id"] for e in model["edges"]} != {e["id"] for e in observed["edges"]}:
        raise ValueError("Operation overlay must not remove or invent observed edges")
    actual = {s["id"]: s["replicas"] for s in model["services"] if s["id"] in SERVICES}
    if actual != meta["replicas"] or not any(e["id"] == ENDPOINT for e in model["endpoints"]):
        raise ValueError("Model does not represent the checked deployment/operation")
    if phase == "candidate":
        base = read(OUT / "baseline/predicate.json")
        if base["predicates"][p["predicate_ref"]] != predicate:
            raise ValueError("Required call identities changed; review and rebaseline explicitly")


def analysis_config(p, predicate, baseline=None):
    config = {
        "schema_version": "1.3", "seed": p["seed"], "trials": p["trials"],
        "predicate_contract": str(predicate),
        "profiles": [{"name": "scenario", "sampling_mode": "independent_replica",
                      "failure_probability": p["failure_probability"],
                      "reliability": {"edge_live_probability": p["edge_live_probability"]}}],
        "sweeps": [{"name": "operation-boundary", "profile": "scenario",
                    "confidence_level": 0.95,
                    "axis": {"type": "independent_replica_failure_probability", "values": p["grid"]},
                    "targets": [{"endpoint_id": ENDPOINT, "slo": p["slo"]}]}],
        "gate": {"mode": "fail", "default_action": "fail", "global_threshold": 0,
                 "endpoint_thresholds": {ENDPOINT: p["slo"]},
                 "boundary_rules": [{"sweep": "operation-boundary", "endpoint_id": ENDPOINT,
                                     "minimum_certified_tolerance": p["minimum_certified_tolerance"]}]},
    }
    if baseline is not None:
        config["baselines"] = [{"name": "accepted", "path": str(baseline)}]
        config["gate"]["boundary_rules"].append({
            "sweep": "operation-boundary", "endpoint_id": ENDPOINT,
            "baseline": "accepted", "max_regression": p["max_regression"]})
        config["importance"] = [{
            "name": "candidate-inspection", "profile": "scenario", "method": "birnbaum",
            "confidence_level": 0.95, "targets": [{"endpoint_id": ENDPOINT}],
            "components": {"kinds": ["service", "edge"]},
        }]
    return config


def check_exit(code, report):
    decision = report["policy_evaluation"]["decision"]
    if (code, decision) not in ((0, "pass"), (2, "fail")):
        raise ValueError(f"Unexpected Sheaft exit/decision: {code}/{decision}")


def analyze(phase, *, policy_value=None, allow_rejected_baseline=False):
    work = OUT / phase
    baseline = OUT / "baseline/model.json" if phase == "candidate" else None
    p = policy() if policy_value is None else policy_value
    config = analysis_config(p, work / "predicate.json", baseline)
    write(work / "analysis.json", config)
    result = run(["sheaft", "run", "--model", work / "model.json",
                  "--analysis", work / "analysis.json", "--out-dir", work / "sheaft",
                  "--why"], allowed=(0, 2))
    report = read(work / "sheaft/report.json")
    check_exit(result.returncode, report)
    write(work / "exit.json", {"code": result.returncode,
                              "decision": report["policy_evaluation"]["decision"]})
    if phase == "baseline" and result.returncode and not allow_rejected_baseline:
        raise ValueError("Baseline does not pass the accepted policy")
    # Candidate exit 2 is retained, not swallowed: enforce runs after report upload.


def report_row(phase):
    work = OUT / phase
    report = read(work / "sheaft/report.json")
    p = next(p for p in report["profiles"] if p["name"] == "scenario")
    boundary = next(b for sweep in report["sweeps"] for b in sweep["boundaries"]
                    if b["endpoint_id"] == ENDPOINT)
    certified = boundary.get("certified_tolerance")
    return {
        **read(work / "provenance.json"),
        **read(work / "exit.json"),
        "availability": p["simulation"]["endpoint_availability"][ENDPOINT],
        "certified_tolerance": certified["axis_value"] if certified else None,
        "boundary_results": report["policy_evaluation"].get("boundary_results", []),
        "reasons": report["policy_evaluation"]["reasons"],
        "importance": report.get("importance", []),
    }


def report():
    lines = ["# MB3R recommendations resilience gate", "",
             "Fresh application executions → Procrustes / Bering / Sheaft → CI decision.", "",
             "| Configuration | Commit | R/C replicas | Availability | Grid tolerance | Decision |",
             "|---|---|---:|---:|---:|---|"]
    rows, errors = [], []
    for phase in ("baseline", "candidate"):
        try:
            row = report_row(phase)
            rows.append(row)
            r = row["replicas"]
            lines.append(f"| {phase} | {row['application_commit'][:10]} | "
                         f"{r['recommendation']}/{r['product-catalog']} | "
                         f"{row['availability']:.6f} | {row['certified_tolerance']} | "
                         f"**{row['decision']}** |")
        except (OSError, ValueError, KeyError, StopIteration) as error:
            errors.append(f"{phase}: no complete acceptance evidence ({error})")
            lines.append(f"| {phase} | — | — | — | — | **ERROR / NOT ACCEPTED** |")
    for row in rows:
        lines += ["", f"## {row['phase'].title()} reasons", ""]
        for reason in row["reasons"]:
            lines.append(f"- {reason['id']}: {reason.get('message', '')}")
        if row["boundary_results"]:
            lines += ["", chr(96) * 3 + "json",
                      json.dumps(row["boundary_results"], indent=2), chr(96) * 3]
        if row["phase"] == "candidate":
            lines += ["", "## Candidate component inspection", "",
                      "| Service | Birnbaum importance | Perfect-component gain |",
                      "|---|---:|---:|"]
            for importance in row["importance"]:
                for endpoint in importance["endpoints"]:
                    if endpoint["endpoint_id"] == ENDPOINT:
                        for component in endpoint["components"]:
                            if component["kind"] == "service" and component["id"] in SERVICES:
                                lines.append(f"| {component['id']} | "
                                             f"{component['birnbaum_importance']:.6f} | "
                                             f"{component['perfect_component_gain']:.6f} |")
    lines += ["", "## Interpretation", "",
              "Healthy HTTP responses and fresh traces validate collection for this fixed USD request. "
              "Replica counts are deployed and checked, but failure probabilities, availability and "
              "SLO margins are model-based. This workflow does not measure replica failover or "
              "execute chaos faults. Importance does not automatically generate corrections.",
              "", "Endpoint thresholds use point estimates; grid certification uses pointwise Wilson "
              "intervals. Regression compares discrete grid metrics. No simultaneous 95% policy "
              "guarantee is claimed.", "",
              "Raw responses, traces, manifests, tool reports and image provenance are in the "
              "short-lived run artifact. No archival fallback is used."]
    lines += ["", *errors]
    write(OUT / "result.json", {"rows": rows, "errors": errors})
    content = "\n".join(lines) + "\n"
    (OUT / "summary.md").write_text(content, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(content)


def enforce():
    for phase in ("baseline", "candidate"):
        work = OUT / phase
        exit_code = read(work / "exit.json")["code"]
        check_exit(exit_code, read(work / "sheaft/report.json"))
        if exit_code:
            print(f"{phase} rejected by Sheaft; see the retained report", flush=True)
            return exit_code
    print("Baseline and candidate pass the model-based resilience policy", flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "observe", "discover", "analyze",
                                         "report", "enforce"])
    parser.add_argument("phase", nargs="?", choices=["baseline", "candidate"])
    args = parser.parse_args()
    if args.action in ("report", "enforce"):
        return globals()[args.action]()
    if not args.phase:
        parser.error("phase is required")
    globals()[args.action](args.phase)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, RuntimeError, StopIteration) as error:
        print(f"MB3R evidence error (fail closed): {error}", file=sys.stderr, flush=True)
        sys.exit(1)
