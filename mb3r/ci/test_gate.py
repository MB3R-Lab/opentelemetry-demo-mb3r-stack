# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gate


class IntegrationContracts(unittest.TestCase):
    def test_replica_map(self):
        self.assertEqual(gate.replicas({"replicas": {"recommendation": 2, "product-catalog": 1}}),
                         {"recommendation": 2, "product-catalog": 1})
        for value in (0, -1, 5, True, 1.5, "2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                gate.replicas({"replicas": {"recommendation": value, "product-catalog": 2}})
        with self.assertRaises(ValueError):
            gate.replicas({"replicas": {"recommendation": 2}})

    def test_success_requires_nonempty_identified_products(self):
        self.assertTrue(gate.response_success(200, '[{"id":"one"}]'))
        for status, body in [(500, '[{"id":"one"}]'), (200, "[]"), (200, "{}"),
                             (200, '[{"id":""}]'), (200, '[{"id":" "}]'),
                             (200, '[{"id":7}]'), (200, "broken")]:
            with self.subTest(status=status, body=body):
                self.assertFalse(gate.response_success(status, body))

    def test_running_replica_counts(self):
        rows = [{"Service": "recommendation", "State": "running"},
                {"Service": "product-catalog", "State": "running"}]
        gate.verify_running(rows, {"recommendation": 1, "product-catalog": 1})
        with self.assertRaises(ValueError):
            gate.verify_running(rows, {"recommendation": 2, "product-catalog": 1})
        rows[0]["State"] = "exited"
        with self.assertRaises(ValueError):
            gate.verify_running(rows, {"recommendation": 1})

    def test_readiness_retries_startup_connection_reset(self):
        with patch.object(gate, "request", side_effect=[
            ConnectionResetError("frontend starting"), {"success": True}
        ]) as request, patch.object(gate.time, "sleep"):
            gate.wait_ready("/api/recommendations?currencyCode=USD", Path("unused"))
            self.assertEqual(request.call_count, 2)

    def test_compose_json_formats(self):
        rows = [{"Service": "recommendation"}, {"Service": "product-catalog"}]
        self.assertEqual(gate.containers(json.dumps(rows)), rows)
        self.assertEqual(gate.containers("\n".join(json.dumps(x) for x in rows)), rows)

    def test_required_calls_are_observed_not_fabricated(self):
        edge = {"id": "actual-id", "from": "frontend", "to": "recommendation",
                "kind": "sync", "blocking": True}
        self.assertEqual(gate.bind_calls({"edges": [edge]}, [["frontend", "recommendation"]]),
                         ["actual-id"])
        for edges in ([], [edge, edge], [dict(edge, kind="async")]):
            with self.subTest(edges=edges), self.assertRaises(ValueError):
                gate.bind_calls({"edges": edges}, [["frontend", "recommendation"]])

    def trace(self):
        return {"data": [{"traceID": "fresh", "processes": {
            str(i): {"serviceName": name}
            for i, name in enumerate(("frontend", "recommendation", "product-catalog"))
        }, "spans": [
            {"traceID": "fresh", "spanID": str(i), "processID": str(i),
             "operationName": name, "startTime": 1_000_000, "duration": 20,
             "references": [], "tags": [{"key": "span.kind", "value": "server"}]}
            for i, name in enumerate(("GET /api/recommendations", "ListRecommendations", "ListProducts"))
        ]}]}

    def test_trace_linkage_and_completeness(self):
        raw = self.trace()
        normalized = gate.normalize_trace(raw, "fresh")
        self.assertEqual(len(normalized), 3)
        self.assertEqual(normalized[0]["trace_id"], "fresh")
        self.assertEqual(normalized[0]["name"], "GET /api/recommendations")
        with self.assertRaises(ValueError):
            gate.normalize_trace(raw, "stale")
        for mutation in ("missing", "duplicate", "wrong-id"):
            changed = copy.deepcopy(raw)
            spans = changed["data"][0]["spans"]
            if mutation == "missing":
                spans.pop()
            elif mutation == "duplicate":
                spans.append(spans[0])
            else:
                spans[0]["traceID"] = "unrelated"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                gate.normalize_trace(changed, "fresh")

    def test_analysis_has_separate_fail_closed_rules(self):
        policy = gate.read(Path(__file__).with_name("policy.json"))
        config = gate.analysis_config(policy, "predicate.json", "baseline.json")
        rules = config["gate"]["boundary_rules"]
        self.assertEqual(config["gate"]["mode"], "fail")
        self.assertEqual(config["gate"]["default_action"], "fail")
        self.assertEqual(len(rules), 2)
        self.assertIn("minimum_certified_tolerance", rules[0])
        self.assertNotIn("max_regression", rules[0])
        self.assertEqual(rules[1]["max_regression"], 0.02)
        self.assertEqual(config["baselines"][0]["path"], "baseline.json")
        self.assertEqual(config["importance"][0]["method"], "birnbaum")

    def test_exit_and_report_must_agree(self):
        gate.check_exit(0, {"policy_evaluation": {"decision": "pass"}})
        gate.check_exit(2, {"policy_evaluation": {"decision": "fail"}})
        for code, decision in ((0, "fail"), (2, "pass"), (0, "warn"),
                               (0, "report"), (1, "pass")):
            with self.subTest(code=code, decision=decision), self.assertRaises(ValueError):
                gate.check_exit(code, {"policy_evaluation": {"decision": decision}})

    def test_enforcement_preserves_rejection_and_missing_evidence_fails(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gate, "OUT", Path(directory)):
            with self.assertRaises(FileNotFoundError):
                gate.enforce()
            for phase in ("baseline", "candidate"):
                gate.write(gate.OUT / phase / "exit.json", {"code": 0})
                gate.write(gate.OUT / phase / "sheaft/report.json",
                           {"policy_evaluation": {"decision": "pass"}})
            self.assertEqual(gate.enforce(), 0)
            gate.write(gate.OUT / "candidate/exit.json", {"code": 2})
            gate.write(gate.OUT / "candidate/sheaft/report.json",
                       {"policy_evaluation": {"decision": "fail"}})
            self.assertEqual(gate.enforce(), 2)


if __name__ == "__main__":
    unittest.main()
