# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
"""Contract checks for the runtime Bering-to-Sheaft snapshot handoff."""

import importlib.util
from pathlib import Path
import unittest


SANITIZER_PATH = Path(__file__).resolve().parents[1] / "sanitizer" / "sanitizer.py"
SPEC = importlib.util.spec_from_file_location("mb3r_snapshot_sanitizer", SANITIZER_PATH)
sanitizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sanitizer)


class SnapshotSanitizerContract(unittest.TestCase):
    def test_typed_evidence_survives_endpoint_filtering(self):
        evidence_key = "bering.observation_evidence.v1"
        evidence_value = '{"schema_version":"1.0.0","warnings":["instance_unknown"]}'
        snapshot = {
            "snapshot_id": "synthetic-window",
            "counts": {"services": 2, "edges": 1, "endpoints": 2},
            "discovery": {
                "services": [{"id": "api", "support": {"observations": 1},
                              "metadata": {"attributes": {evidence_key: evidence_value}}}],
                "edges": [{"id": "observed-edge", "metadata": {
                    "attributes": {evidence_key: evidence_value}}}],
            },
            "model": {
                "metadata": {"source_type": "bering"},
                "services": [
                    {"id": "api"},
                    {"id": "worker", "metadata": {"attributes": {"owner": "example"}}},
                ],
                "edges": [{"id": "observed-edge", "from": "api", "to": "worker"}],
                "endpoints": [
                    {"id": "api:GET /ready", "entry_service": "api"},
                    {"id": "api:GET /missing", "entry_service": "api"},
                ],
            },
        }
        sanitized = sanitizer.sanitize_snapshot(
            snapshot, {"api:GET /ready": ["api", "worker"],
                       "api:GET /missing": ["unknown-service"]})

        self.assertEqual([item["id"] for item in sanitized["model"]["endpoints"]],
                         ["api:GET /ready"])
        self.assertEqual(sanitized["discovery"]["services"][0]["metadata"]["attributes"][evidence_key],
                         evidence_value)
        self.assertEqual(sanitized["discovery"]["edges"][0]["metadata"]["attributes"][evidence_key],
                         evidence_value)
        self.assertEqual(sanitized["discovery"], snapshot["discovery"])
        self.assertEqual(sanitized["counts"], {"services": 2, "edges": 1, "endpoints": 1})
        self.assertEqual(len(snapshot["model"]["endpoints"]), 2)


if __name__ == "__main__":
    unittest.main()
