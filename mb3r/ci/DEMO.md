# Parameterized application demo

Open [Actions → MB3R parameterized demo](https://github.com/MB3R-Lab/opentelemetry-demo-mb3r-stack/actions/workflows/mb3r-demo.yml)
and choose **Run workflow**. Use the main branch to publish a browser-readable
report to [GitHub Pages](https://mb3r-lab.github.io/opentelemetry-demo-mb3r-stack/).
The selected workflow branch is the candidate application revision. The baseline
revision is selected separately. Both revisions must contain the recommendations
CI deployment profile.

This is the same OTel fork as the older runtime-monitoring demo, not a new
repository. The workflow uses the standalone application CI profile in this
directory and the pinned Procrustes 0.1.0 / Bering 1.0.0 / Sheaft 1.3.1 CLIs.
It does not use the old dashboard, exporter or paper-reproduction entrypoint.

## Parameters

Probabilities are fractions, not percentage numbers: enter 0.95 for 95%.

| Input | Default | Meaning |
|---|---|---|
| baseline_ref | main | Baseline application branch or commit |
| baseline_replicas | 2/2 | recommendation/catalog replicas, each from 1 to 4 |
| candidate_replicas | 2/1 | Same two counts for the candidate |
| slo | 0.95 | Required operation availability, greater than 0 and at most 1 |
| failure_probability | 0.05 | Independent failure probability per eligible replica, greater than 0 and at most 1 |
| edge_live_probability | 0.995 | Modeled channel liveness |
| minimum_certified_tolerance | 0.10 | Minimum certified grid tolerance, must be a point on the versioned grid |
| max_regression | 0.02 | Maximum drop in certified grid tolerance, 0.02 = 2 percentage points |
| trials | 40000 | Trials per calculation, integer from 1000 to 100000 |
| seed | 2719 | Integer from 1 to 2147483647 |

The operation is fixed to GET /api/recommendations?currencyCode=USD, requiring
HTTP 200 with a nonempty list of identified products. The three required calls
and the sweep grid are versioned in policy.json, not configurable through this
form. The current grid is 0, .01, .02, .03, .04, .05, .06, .08, .10, .12, .14,
.16, .18, .20, .25. Both configurations use the same selected policy.

The released Sheaft normalizes a zero profile failure probability and seed zero
to defaults. The form rejects these two values instead of silently analyzing
different inputs. Zero failure probability is still explicitly evaluated in
the sweep, where the tool supports it.

Manual inputs do not edit tracked files. They produce effective policy and
deployment snapshots with checksums in the run evidence. The same replica
override controls the actual Compose deployment and the discovered model;
actual running counts are checked.

## Example comparisons

Keep baseline 2/2 and all other defaults; change only candidate_replicas:

| Candidate | Question | Expected model outcome |
|---|---|---|
| 2/1 | What if one catalog replica is removed? | Reject |
| 3/1 | Does one extra recommendation replica compensate? | Still reject |
| 2/2 | Does restoring the catalog replica satisfy the policy? | Pass |

Each manual run compares one pair, not all three alternatives at once. These
are expectations, not hardcoded decisions. The workflow deploys both revisions,
collects fresh HTTP responses and Jaeger traces, and invokes all three CLIs.
The comparisons use a fixed USD request and healthy observation runs. They do
not inject faults or measure replica failover.

You can also change the SLO or assumed failure probabilities to explore a
different engineering requirement. This is not a way to make the required PR
check pass: that check still uses the base commit's policy with no manual
overrides. The manual job has a different check name.

## Report

The report includes:

- baseline/candidate commits, replica counts, model availability and decisions;
- availability versus replica failure probability, with pointwise 95% Wilson
  intervals and the SLO line;
- concrete SLO, minimum-tolerance and regression reasons;
- component Birnbaum importance, its intervals and perfect-component gains;
- selected parameters, checksums, toolchain image and fresh evidence counts;
- all evaluated sweep points, including those below the SLO.

The graph connects sampled points only as a visual guide. Certification uses
the discrete grid. It does not estimate an exact continuous threshold, and
pointwise intervals do not provide a simultaneous 95% policy guarantee.
Perfect-component gain is not the effect of adding a replica.

The selected-point estimate and the sweep have separate tool-generated random
streams, so estimates at the same failure probability can differ. Full sweep
seeds and outputs are preserved in result.json and the raw tool reports.

Every run produces a GitHub Summary and a downloadable **mb3r-demo-report**
artifact containing a self-contained index.html and result.json; no external
JavaScript or plotting service is needed. The larger **mb3r-demo-evidence**
artifact contains raw responses, traces, manifests and CLI reports. Both expire
after three days to bound Actions storage.

Main-branch runs also publish the derived report to GitHub Pages, even for a
rejected policy or incomplete evidence. Other branches get downloadable
artifacts only. Pages shows the **latest published run**, not a report archive;
its header includes the exact run ID and link. Publishing a report is not
deploying the application. Raw responses and logs are not published to Pages.

A policy failure remains a **red** exploratory run, even when report publication
succeeds. If the exploratory baseline itself fails, the candidate is still
calculated and both results are shown, but the combined run cannot pass.
Missing evidence is an ERROR, never a green decision.

For another fork, enable GitHub Pages with **GitHub Actions** as its source and
allow the main branch to deploy to the github-pages environment. Publication
uses a separate job with pages-write and OIDC permissions; the analysis job
only has contents-read. No application runs or experiments need to be launched
on the reviewer's local machine.
