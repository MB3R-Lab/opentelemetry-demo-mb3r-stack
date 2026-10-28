# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
import copy
import json
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import demo
import demo_report
import gate

MANUAL_ENV = {
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_WORKFLOW": demo.WORKFLOW_NAME,
}


class ManualDemoContracts(unittest.TestCase):
    def setUp(self):
        self.policy = gate.read(Path(__file__).with_name("policy.json"))
        self.inputs = {key: self.policy[key] for key in demo.NUMBERS}
        self.inputs.update(
            baseline_ref="main", baseline_replicas="2/2", candidate_replicas="2/1"
        )

    def fixture(self):
        config = demo.configuration(self.inputs, self.policy)
        point = {
            "axis_value": 0.05,
            "endpoint_availability": {gate.ENDPOINT: 0.94},
            "endpoint_confidence": {
                gate.ENDPOINT: {"lower_bound": 0.93, "upper_bound": 0.95}
            },
        }
        rows = []
        for phase in ("baseline", "candidate"):
            rows.append(
                {
                    "phase": phase,
                    "application_commit": "a" * 40,
                    "replicas": config["replicas"][phase],
                    "availability": 0.94,
                    "certified_tolerance": 0.03,
                    "decision": "fail",
                    "code": 2,
                    "boundary_results": [
                        {
                            "status": "fail",
                            "certified_tolerance": 0.03,
                            "minimum_certified_tolerance": 0.10,
                        }
                    ],
                    "reasons": [
                        {"id": "endpoint_below_threshold", "message": "Below 0.95"}
                    ],
                    "importance": [],
                    "requests": 2,
                    "spans": 42,
                    "sweep": {"points": [copy.deepcopy(point)], "seed": 123},
                }
            )
        return {
            "configuration": config,
            "endpoint": gate.ENDPOINT,
            "rows": rows,
            "errors": [],
            "run_id": "123",
            "run_url": "https://github.com/example/demo/actions/runs/123",
            "attempt": "1",
            "workflow_commit": "a" * 40,
        }

    def test_parameters_are_explicit_and_do_not_mutate_base_policy(self):
        original = copy.deepcopy(self.policy)
        self.inputs.update(
            slo="0.96", trials="5000", seed="7", candidate_replicas="3/1"
        )
        config = demo.configuration(self.inputs, self.policy)
        self.assertEqual(config["policy"]["slo"], 0.96)
        self.assertEqual(config["policy"]["trials"], 5000)
        self.assertEqual(config["replicas"]["candidate"]["recommendation"], 3)
        self.assertEqual(config["policy"]["grid"], original["grid"])
        self.assertEqual(
            config["policy"]["required_call_pairs"], original["required_call_pairs"]
        )
        self.assertEqual(self.policy, original)

    def test_invalid_inputs_fail_before_deployment(self):
        for key, value in [
            ("slo", 0),
            ("slo", 1.01),
            ("slo", "nan"),
            ("slo", "inf"),
            ("slo", True),
            ("failure_probability", -0.01),
            ("failure_probability", 0),
            ("edge_live_probability", 2),
            ("trials", 999),
            ("trials", 100001),
            ("trials", 4000.5),
            ("seed", -1),
            ("seed", 0),
            ("seed", 3.2),
            ("minimum_certified_tolerance", 0.3),
            ("minimum_certified_tolerance", 0.07),
            ("max_regression", -1),
            ("baseline_replicas", "0/2"),
            ("candidate_replicas", "2/5"),
            ("candidate_replicas", "2/1; echo bad"),
            ("baseline_ref", ""),
        ]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                demo.configuration(dict(self.inputs, **{key: value}), self.policy)
        with self.assertRaises(ValueError):
            demo.configuration(dict(self.inputs, unknown="value"), self.policy)
        missing = dict(self.inputs)
        del missing["seed"]
        with self.assertRaises(ValueError):
            demo.configuration(missing, self.policy)

    def test_override_entrypoint_refuses_pr_and_other_workflow(self):
        for event, workflow in [
            ("pull_request", demo.WORKFLOW_NAME),
            ("workflow_dispatch", "MB3R resilience gate"),
        ]:
            with patch.dict(
                os.environ, {"GITHUB_EVENT_NAME": event, "GITHUB_WORKFLOW": workflow}
            ):
                with self.assertRaises(ValueError):
                    demo.require_manual()
        with patch.dict(os.environ, MANUAL_ENV):
            demo.require_manual()

    def test_required_gate_policy_ignores_manual_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            gate.write(base / "mb3r/ci/policy.json", self.policy)
            with patch.dict(
                os.environ,
                {"BASE_SOURCE": str(base), "DEMO_INPUTS_JSON": '{"slo":0.1}'},
            ):
                self.assertEqual(gate.policy(), self.policy)

    def test_override_controls_manifest_and_effective_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            profile, work = source / "mb3r/ci", source / "out"
            original = {"replicas": {"recommendation": 2, "product-catalog": 2}}
            gate.write(profile / "deployment.json", original)
            gate.write(profile / "policy.json", self.policy)
            gate.write(profile / "recommendations.tracetest.yaml", {})
            gate.write(work / "procrustes/report.json", {"goal_status": "EXPECTED"})
            override = {"recommendation": 3, "product-catalog": 1}
            resolved = {
                "services": {
                    s: {"deploy": {"replicas": n}} for s, n in override.items()
                }
            }
            context = (source, profile, work, "a" * 40, {}, ["docker", "compose"])
            policy = dict(self.policy, slo=0.96)
            with patch.object(gate, "context", return_value=context), patch.object(
                gate, "base_source", return_value=source
            ), patch.object(
                gate, "run", return_value=SimpleNamespace(stdout=json.dumps(resolved))
            ):
                gate.prepare(
                    "candidate", replica_override=override, policy_value=policy
                )
            self.assertEqual(
                gate.read(work / "effective-deployment.json")["replicas"], override
            )
            self.assertEqual(gate.read(work / "effective-policy.json")["slo"], 0.96)
            self.assertEqual(gate.read(profile / "deployment.json"), original)
            self.assertEqual(gate.read(profile / "policy.json"), self.policy)
            self.assertTrue(
                gate.read(work / "provenance.json")["manual_replica_override"]
            )

    def test_baseline_rejection_is_only_continued_explicitly_for_demo(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            gate, "OUT", Path(directory)
        ), patch.object(gate, "run", return_value=SimpleNamespace(returncode=2)):
            gate.write(
                gate.OUT / "baseline/sheaft/report.json",
                {"policy_evaluation": {"decision": "fail"}},
            )
            with self.assertRaisesRegex(ValueError, "Baseline does not pass"):
                gate.analyze("baseline", policy_value=self.policy)
            gate.analyze(
                "baseline", policy_value=self.policy, allow_rejected_baseline=True
            )
            self.assertEqual(gate.enforce(), 2)

    def test_execution_uses_selected_counts_and_same_policy(self):
        config = demo.configuration(self.inputs, self.policy)
        with patch.dict(os.environ, MANUAL_ENV), patch.object(
            gate, "read", return_value=config
        ), patch.object(gate, "prepare") as prepare, patch.object(
            gate, "analyze"
        ) as analyze:
            demo.execute("prepare", "candidate")
            prepare.assert_called_once_with(
                "candidate",
                policy_value=config["policy"],
                replica_override=config["replicas"]["candidate"],
            )
            demo.execute("analyze", "baseline")
            analyze.assert_called_once_with(
                "baseline", policy_value=config["policy"], allow_rejected_baseline=True
            )

    def test_report_has_real_data_svg_intervals_inputs_and_no_external_dependencies(
        self,
    ):
        result = self.fixture()
        html = demo_report.render(result)
        for value in (
            "94.00%",
            "3.00%",
            "0.95",
            "FAIL",
            "not release approval",
            "95% Wilson",
            "All evaluated sweep points",
            "42",
        ):
            self.assertIn(value, html)
        svg = re.search(r"(<svg.*?</svg>)", html, re.S).group(1)
        ET.fromstring(svg)
        self.assertEqual(svg.count("<polyline"), 2)
        self.assertEqual(svg.count("<circle"), 2)
        self.assertNotIn("<script", html)
        self.assertNotIn('src="https://', html)

    def test_html_escapes_ref_and_error_text(self):
        result = self.fixture()
        result["configuration"]["inputs"][
            "baseline_ref"
        ] = '<img src=x onerror="alert(1)">'
        result["errors"] = ["<script>alert(2)</script>"]
        html = demo_report.render(result)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(demo_report.verdict(result), "ERROR")

    def test_missing_evidence_and_null_tolerance_are_not_green(self):
        result = self.fixture()
        result["rows"][0]["certified_tolerance"] = None
        self.assertIn("not certified", demo_report.render(result))
        result["rows"] = []
        result["errors"] = ["No evidence"]
        self.assertIn("ERROR", demo_report.render(result))
        self.assertIn("ERROR", demo_report.summary(result))

    def test_pass_requires_both_rows_and_no_errors(self):
        result = self.fixture()
        for row in result["rows"]:
            row["decision"] = "pass"
        self.assertEqual(demo_report.verdict(result), "PASS")
        result["rows"][0]["decision"] = "fail"
        self.assertEqual(demo_report.verdict(result), "FAIL")
        result["rows"].pop()
        self.assertEqual(demo_report.verdict(result), "ERROR")

    def test_missing_outputs_still_produce_downloadable_error_report(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            gate, "OUT", Path(directory)
        ), patch.dict(
            os.environ,
            {**MANUAL_ENV, "GITHUB_STEP_SUMMARY": str(Path(directory) / "step.md")},
        ):
            demo.report()
            self.assertTrue((gate.OUT / "site/index.html").is_file())
            self.assertEqual(
                demo_report.verdict(gate.read(gate.OUT / "site/result.json")), "ERROR"
            )
            self.assertIn("ERROR", (gate.OUT / "step.md").read_text(encoding="utf-8"))

    def test_workflows_keep_separate_names_and_no_parameter_path_in_pr_gate(self):
        root = Path(__file__).resolve().parents[2]
        required = (root / ".github/workflows/mb3r-resilience.yml").read_text()
        manual = (root / ".github/workflows/mb3r-demo.yml").read_text()
        self.assertIn("name: MB3R / recommendations gate", required)
        self.assertNotIn("demo.py", required)
        self.assertNotIn("DEMO_INPUTS_JSON", required)
        self.assertNotIn("pull_request:", manual)
        self.assertNotIn("name: MB3R / recommendations gate", manual)
        self.assertIn("github.ref == 'refs/heads/main'", manual)


if __name__ == "__main__":
    unittest.main()
