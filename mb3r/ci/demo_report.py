# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
"""Dependency-free presentation of released-tool outputs; no analytical solver."""
from html import escape
import json
import math


def esc(value):
    return escape(str(value), quote=True)


def percent(value):
    return "not certified" if value is None else f"{100 * value:.2f}%"


def verdict(result):
    if result["errors"] or len(result["rows"]) != 2:
        return "ERROR"
    return "PASS" if all(row["decision"] == "pass" for row in result["rows"]) else "FAIL"


def components(row, endpoint):
    return [component for importance in row.get("importance", [])
            for item in importance["endpoints"] if item["endpoint_id"] == endpoint
            for component in item["components"]]


def table(headers, rows):
    return ('<div class="scroll"><table><thead><tr>'
            + "".join(f"<th>{esc(h)}</th>" for h in headers)
            + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>"
                      for row in rows)
            + "</tbody></table></div>")


def chart(rows, endpoint, slo):
    points = [p for row in rows for p in row["sweep"]["points"]]
    if not points:
        return "<p>No sweep evidence available.</p>"
    xmax = max(p["axis_value"] for p in points) or 1
    low = min([p["endpoint_confidence"][endpoint]["lower_bound"] for p in points] + [slo])
    ymin = max(0, math.floor((low - 0.025) * 20) / 20)
    x = lambda value: 75 + 780 * value / xmax
    y = lambda value: 330 - 285 * (value - ymin) / (1 - ymin)
    svg = ['<svg viewBox="0 0 920 410" role="img" aria-labelledby="curve-title curve-desc">',
           '<title id="curve-title">Availability versus independent replica failure probability</title>',
           '<desc id="curve-desc">Baseline and candidate estimates at the evaluated grid points, '
           'with pointwise 95 percent Wilson intervals and a horizontal SLO line. '
           'Connecting lines are visual guides, not interpolated certificates.</desc>']
    for i in range(6):
        value = ymin + (1 - ymin) * i / 5
        svg += [f'<line x1="75" y1="{y(value):.2f}" x2="855" y2="{y(value):.2f}" class="grid"/>',
                f'<text x="65" y="{y(value) + 4:.2f}" text-anchor="end">{100 * value:.0f}%</text>']
    for i in range(6):
        value = xmax * i / 5
        svg.append(f'<text x="{x(value):.2f}" y="355" text-anchor="middle">{100 * value:.1f}%</text>')
    svg += [f'<line x1="75" y1="{y(slo):.2f}" x2="855" y2="{y(slo):.2f}" class="slo"/>',
            f'<text x="855" y="{y(slo) - 8:.2f}" text-anchor="end">SLO {percent(slo)}</text>',
            '<text x="75" y="23">Model-based operation availability</text>',
            '<text x="465" y="390" text-anchor="middle">Independent failure probability per eligible replica</text>']
    for row in rows:
        color = "#155eef" if row["phase"] == "baseline" else "#c2410c"
        vertices = " ".join(f'{x(p["axis_value"]):.2f},{y(p["endpoint_availability"][endpoint]):.2f}'
                            for p in row["sweep"]["points"])
        svg.append(f'<polyline points="{vertices}" fill="none" stroke="{color}" stroke-width="2"/>')
        for p in row["sweep"]["points"]:
            confidence = p["endpoint_confidence"][endpoint]
            px = x(p["axis_value"])
            py = y(p["endpoint_availability"][endpoint])
            lo, hi = y(confidence["lower_bound"]), y(confidence["upper_bound"])
            label = (f'{row["phase"]}: failure={percent(p["axis_value"])}, '
                     f'availability={percent(p["endpoint_availability"][endpoint])}, '
                     f'Wilson=[{percent(confidence["lower_bound"])}, '
                     f'{percent(confidence["upper_bound"])}]')
            svg += [f'<g stroke="{color}"><title>{esc(label)}</title>',
                    f'<line x1="{px:.2f}" y1="{lo:.2f}" x2="{px:.2f}" y2="{hi:.2f}"/>',
                    f'<line x1="{px-3:.2f}" y1="{lo:.2f}" x2="{px+3:.2f}" y2="{lo:.2f}"/>',
                    f'<line x1="{px-3:.2f}" y1="{hi:.2f}" x2="{px+3:.2f}" y2="{hi:.2f}"/>',
                    f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3" fill="{color}"/></g>']
    return "".join(svg) + '</svg><p class="legend"><span>● Baseline</span><span>● Candidate</span></p>'


def summary(result):
    config = result.get("configuration", {})
    policy = config.get("policy", {})
    fence = chr(96) * 3
    lines = ["# MB3R parameterized demo — " + verdict(result), "",
             "**Manual exploration, not release approval.** The required PR check is unchanged.",
             "", f"Operation: {result['endpoint']}", "",
             "| Configuration | Replicas R/C | Model availability | Certified grid tolerance | Decision |",
             "|---|---:|---:|---:|---|"]
    for row in result["rows"]:
        counts = row["replicas"]
        lines.append(f"| {row['phase']} | {counts['recommendation']}/{counts['product-catalog']} | "
                     f"{percent(row['availability'])} | {percent(row['certified_tolerance'])} | "
                     f"{row['decision'].upper()} |")
    lines += ["", "## Selected parameters", "", fence + "json", json.dumps(config.get("inputs", {}), indent=2),
              fence, "", f"Versioned sweep grid: {policy.get('grid', [])}", ""]
    for row in result["rows"]:
        lines += [f"## {row['phase'].title()} reasons", ""]
        lines += [f"- {r['id']}: {r.get('message', '')}" for r in row["reasons"]]
    lines += ["", *result["errors"], "",
              "The downloadable mb3r-demo-report artifact contains index.html and result.json. "
              "Main-branch runs also publish the latest report to GitHub Pages; "
              "the Pages deployment job links the site. The site may later show a newer run.",
              "", "Healthy requests/traces are execution evidence; availability and tolerance "
              "are model-based, not measured uptime or failover. Pointwise Wilson intervals "
              "do not give a simultaneous 95% policy guarantee.", ""]
    return "\n".join(lines)


def render(result):
    config = result.get("configuration", {})
    policy = config.get("policy", {})
    rows, endpoint = result["rows"], result["endpoint"]
    status = verdict(result)
    sections = [
        '<header><div class="eyebrow">PROCRUSTES → BERING → SHEAFT · MANUAL EXPLORATION</div>',
        f'<h1>Recommendations resilience <span class="badge {status.lower()}">{status}</span></h1>',
        '<p>Compare deployed configurations under an explicit model-based failure policy.</p>',
        f'<p><code>{esc(endpoint)}</code></p>',
        f'<p><a href="{esc(result["run_url"])}">GitHub run {esc(result["run_id"])}</a>'
        f' · attempt {esc(result["attempt"])} · <a href="result.json">Report JSON</a></p></header>',
        '<aside><strong>Exploratory result, not release approval.</strong> These parameters do not '
        'change or satisfy the required pull-request gate. This page shows the latest published '
        'manual run; check its run ID. Previous reports remain in their short-lived run artifacts.</aside>',
    ]
    if result["errors"]:
        sections.append('<section><h2>Incomplete evidence</h2><ul>' + "".join(
            f"<li>{esc(error)}</li>" for error in result["errors"]) + "</ul></section>")
    sections += ['<section><h2>Decision at the selected failure probability</h2>',
                 table(["Configuration", "Commit", "Replicas R/C", "Model availability",
                        "Certified grid tolerance", "Decision"], [
                     [r["phase"], r["application_commit"][:10],
                      f'{r["replicas"]["recommendation"]}/{r["replicas"]["product-catalog"]}',
                      percent(r["availability"]), percent(r["certified_tolerance"]),
                      r["decision"].upper()] for r in rows]),
                 '<p>R/C = recommendation / product-catalog. Tolerance is a discrete certified '
                 'grid value, not a measured continuous failure threshold.</p></section>']
    if policy:
        sections += ['<section><h2>Availability as failures increase</h2>',
                     chart(rows, endpoint, policy["slo"]),
                     '<p>Bars are pointwise 95% Wilson intervals. Lines only connect evaluated '
                     'points. The selected-point calculation and sweep use separate tool-generated '
                     'random streams; their estimates need not be identical at the same probability.</p></section>']
    sections.append('<section><h2>Why this decision?</h2>')
    for row in rows:
        sections += [f'<h3>{esc(row["phase"].title())} — {esc(row["decision"].upper())}</h3><ul>']
        sections += [f'<li><code>{esc(reason["id"])}</code>: {esc(reason.get("message", ""))}</li>'
                     for reason in row["reasons"]]
        sections += ['</ul>', table(
            ["Rule status", "Tolerance", "Minimum", "Baseline tolerance", "Change", "Allowed drop"],
            [[b["status"], percent(b.get("certified_tolerance")),
              percent(b["minimum_certified_tolerance"]) if "minimum_certified_tolerance" in b else "—",
              percent(b["reference_tolerance"]) if "reference_tolerance" in b else "—",
              f'{100*b["regression"]:+.2f} pp' if "regression" in b else "—",
              f'{100*b["max_regression"]:.2f} pp' if "max_regression" in b else "—"]
             for b in row["boundary_results"]])]
    sections.append('</section><section><h2>Candidate component importance</h2>')
    candidate = next((r for r in rows if r["phase"] == "candidate"), None)
    if candidate:
        sections.append(table(
            ["Component", "Kind", "Birnbaum importance", "95% interval", "Perfect-component gain"],
            [[c["id"], c["kind"], f'{c["birnbaum_importance"]:.6f}',
              f'[{c["confidence"]["lower_bound"]:.6f}, {c["confidence"]["upper_bound"]:.6f}]',
              f'{100*c["perfect_component_gain"]:.4f} pp']
             for c in components(candidate, endpoint)]))
    else:
        sections.append("<p>No candidate importance evidence available.</p>")
    sections += ['<p>Importance is conditional sensitivity under the selected profile. '
                 'Perfect-component gain assumes making that component always live; it is not '
                 'the predicted gain of adding a replica. Test each proposed correction separately.</p></section>',
                 '<section><h2>Inputs and provenance</h2>',
                 table(["Manual parameter", "Selected value"], list(config.get("inputs", {}).items())),
                 f'<p>Fixed request: <code>{esc(policy.get("request_path", "unavailable"))}</code>. '
                 'Failure-eligible replicas: recommendation and product-catalog only. '
                 'Frontend, PostgreSQL, flagd and telemetry are held healthy. '
                 'Required channel liveness is modeled independently.</p>',
                 f'<p>Versioned grid: {esc(policy.get("grid", []))}</p>',
                 table(["Configuration", "Fresh HTTP requests", "Trace spans", "Application commit"],
                       [[r["phase"], r.get("requests", "unavailable"), r.get("spans", "unavailable"),
                         r["application_commit"]] for r in rows]),
                 '<details><summary>Effective policy and checksums</summary><pre>'
                 + esc(json.dumps({"policy": policy, "provenance": [
                     {k: r[k] for k in (
                         "phase", "application_commit", "toolchain_image", "deployment_sha256",
                         "policy_sha256", "effective_deployment_sha256", "effective_policy_sha256")
                      if k in r} for r in rows]}, indent=2)) + '</pre></details></section>',
                 '<section><h2>All evaluated sweep points</h2>']
    for row in rows:
        sections += [f'<details><summary>{esc(row["phase"].title())} grid and confidence intervals</summary>',
                     table(["Replica failure probability", "Availability", "Wilson lower", "Wilson upper"],
                           [[percent(p["axis_value"]), percent(p["endpoint_availability"][endpoint]),
                             percent(p["endpoint_confidence"][endpoint]["lower_bound"]),
                             percent(p["endpoint_confidence"][endpoint]["upper_bound"])]
                            for p in row["sweep"]["points"]]), '</details>']
    sections += ['</section><footer><strong>Interpretation limits.</strong> Live healthy requests '
                 'and traces establish observations for this fixed USD operation. Replica counts '
                 'are deployed and checked, but this run does not inject chaos faults, calibrate '
                 'failure probabilities or measure failover. Availability is modeled, not observed '
                 'uptime. Endpoint acceptance uses a point estimate; grid certification uses pointwise '
                 'Wilson intervals; regression compares discrete metrics. No simultaneous 95% '
                 'policy guarantee is claimed. Raw evidence is retained for three days.</footer>']
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>MB3R manual resilience report</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fb;color:#182230;font:16px/1.6 system-ui,sans-serif}
main{max-width:1140px;margin:auto;padding:32px 24px}header{padding:26px 0}h1{font-size:36px;line-height:1.2}
h2{font-size:23px;margin-top:0}h3{font-size:18px}a{color:#155eef}p{color:#475467}
.eyebrow{font-size:12px;letter-spacing:.13em;color:#475467;font-weight:700}
section,aside{background:white;border:1px solid #dce3ee;border-radius:14px;padding:24px;margin:18px 0}
aside{background:#fff8e7;border-color:#e9d4a0}.badge{font-size:16px;padding:7px 12px;border-radius:7px;vertical-align:middle}
.pass{background:#d9fbe8;color:#05603a}.fail,.error{background:#fee4e2;color:#a92318}
table{width:100%;border-collapse:collapse;font-size:14px;text-align:left}
th{color:#475467;background:#f8fafc}th,td{padding:11px 13px;border-bottom:1px solid #e4e7ec}
td{font-variant-numeric:tabular-nums}td,code{overflow-wrap:anywhere}.scroll{overflow-x:auto}
svg{width:100%;height:auto}svg text{fill:#475467;font:13px system-ui,sans-serif}
.grid{stroke:#e4e7ec}.slo{stroke:#475467;stroke-dasharray:6 4}
.legend{display:flex;gap:25px;justify-content:center;margin:0}.legend span:first-child{color:#155eef}
.legend span:last-child{color:#c2410c}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}
details{margin:15px 0}summary{cursor:pointer;font-weight:600}footer{font-size:14px;color:#475467;padding:22px 4px}
@media(max-width:600px){main{padding:15px}h1{font-size:27px}section,aside{padding:16px}}
</style></head><body><main>""" + "".join(sections) + "</main></body></html>\n"
