# Recommendations resilience gate

<!-- markdownlint-disable MD013 -->

This directory contains application CI profiles for OpenTelemetry Demo. They
operate independently of the runtime-monitoring overlay and do not launch
Grafana or its exporter.

Two entry points are available:

| Workflow | Purpose | Parameters |
| --- | --- | --- |
| MB3R resilience gate | Required PR acceptance check | Policy from the base commit |
| [MB3R parameterized demo](DEMO.md) | Manual exploration with HTML/SVG report | Replica counts, SLO, failure model, trials and seed |

The manual demo never satisfies or replaces the required PR status check.

The pipeline invokes the public Procrustes, Bering and Sheaft CLIs directly.
Open **Actions > MB3R resilience gate > Run workflow**, or open a pull request
against this fork's main branch.
No local application deployment is needed.

## What a run does

1. Resolve the candidate commit and the PR base commit (or the manual baseline
   ref). Read each commit's mb3r/ci/deployment.json.
2. Run Procrustes on the resolved deployment and the same recommendations
   operation for both versions. Require EXPECTED, not merely exit zero.
3. Build and deploy the baseline on the GitHub runner, check actual running
   replica counts, execute two successful nonempty USD requests, retain their
   HTTP responses and fresh Jaeger traces, then remove that disposable deployment.
4. Use Bering to discover the operation. Bind all three mandatory calls to
   actual discovered edge IDs, retaining every discovered edge.
5. Evaluate the baseline using Sheaft. Then independently deploy, observe,
   discover and analyze the candidate. Both use the **base commit's policy**.
6. Publish the decision, reasons and Birnbaum component inspection in the
   GitHub job summary. Retain raw evidence for three days, including on failure.
7. Propagate Sheaft's exit code: 0 for pass, 2 for policy rejection, 1 for
   an evidence/configuration/integration error. Missing evidence cannot pass.

The checked-in policy uses SLO 0.95, independent eligible-replica failure 0.05,
channel liveness 0.995, 40,000 trials and seed 2719. The grid, minimum certified
tolerance 0.10 and maximum regression 0.02 are explicit in policy.json.
Frontend, PostgreSQL, flagd and telemetry are held healthy.

## Demonstrating a configuration review

The accepted declaration is recommendation/catalog = **2/2**. Edit only
deployment.json in a PR; do not change the policy to obtain a green check.

| Candidate | Expected model-based outcome |
| --- | --- |
| 2/1: remove a catalog replica | Reject: availability and grid tolerance below requirements |
| 3/1: add a recommendation replica to the reduced configuration | Still reject |
| 2/2: restore the catalog replica | Pass |

These are expectations from the previously checked operation, not hardcoded
decisions. Every run obtains fresh observations and invokes the released tools.
The same replica declaration controls the real disposable Compose deployment
and the Bering model; the wrapper verifies both. Importance assists inspection,
but each proposed change must be independently recalculated.

The named check is **MB3R / recommendations gate**. It is configured as a required
status check on this fork's main branch, including administrators. If copying
the integration, configure the same restriction. A red workflow alone is
not a merge restriction. For a delivery pipeline, make promotion depend on this
check; this example does not deploy an application to production.

## Toolchain and separation of responsibilities

The workflow extracts the three public CLI binaries from the immutable
[v1.3.5 toolchain image](https://github.com/MB3R-Lab/mb3r-software-complex-repro/pkgs/container/mb3r-software-complex-repro):
Procrustes 0.1.0, Bering 1.2.0 and Sheaft 1.3.2. It does **not** invoke the
image's reproduction entrypoint or use archived traces or reference decisions.
The source pins travel with each run as toolchain-lock.json.

gate.py is standard-library integration glue: it prepares CLI inputs, captures
HTTP/Jaeger evidence, validates the handoffs and formats reports. It implements
no availability estimator, SLO solver or component-importance calculation.

The mb3r/docker-compose.mb3r.yml runtime overlay uses a separate Sheaft pin and
an exporter-derived verdict; neither participates in this gate.
Published-study reproduction and operation fault-state validation are in the
[separate artifact](https://github.com/MB3R-Lab/mb3r-software-complex-repro).

## Limits and fail-closed behavior

- The runner builds the selected application sources, including unchanged
  recommendation handlers. The profile deploys only this operation's services.
- The fixed USD request, fixed database seed and disabled recommendation cache
  match the operation whose required-call semantics were checked previously.
  These CI runs collect healthy observations, not another chaos experiment.
- Running replica counts are checked, but load balancing, failover, operational
  failure probabilities and general handler equivalence are not measured.
- Ordinary endpoint rules use point estimates. Sweep certification uses
  pointwise Wilson intervals. Regression compares discrete certified grid
  metrics, not a confidence interval for a continuous-threshold difference.
- A missing trace, ambiguous/missing required call, changed required-call
  identity, invalid baseline or unexpected tool result prevents acceptance.
  There is no fallback to a committed dependency graph.
- Application dependencies use the versions in the selected source revision;
  infrastructure images use version tags. Run artifacts retain local image IDs.
  Only the analysis toolchain is guaranteed by an immutable image digest here.
- This is a demonstration CI profile. Changes to gate code/workflows still need
  trusted review; a status check is not a security boundary against maintainers
  allowed to rewrite the check itself.

No Actions build cache is uploaded. Baseline/candidate Docker builds reuse
layers only within one runner. The lightweight configuration PRs do not trigger
the upstream all-service build matrix; application-source changes still do.
