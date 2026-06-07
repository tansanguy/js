from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = PROJECT_ROOT / "09-1 B4 Optimization S1forced/audit_09_run_conditions.py"
MANIFEST_PATH = PROJECT_ROOT / "configs/compact_v9_B04_B4_active_inputs.json"


def load_audit():
    spec = importlib.util.spec_from_file_location("audit_09_run_conditions", AUDIT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_09_run_conditions"] = module
    spec.loader.exec_module(module)
    return module


class B409RunConditionsAuditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = load_audit()

    def test_manifest_declares_canonical_profile_and_optimizer_policy(self):
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        self.assertEqual(payload["canonical_profile"], "B04_B4_S1_FORCED_OPTIMIZATION")
        self.assertEqual(payload["net_file"], self.audit.CANONICAL["net_file"])
        self.assertIn("net_file_sha256", payload)
        self.assertEqual(payload["background_route"], self.audit.CANONICAL["background_route"])
        self.assertIn("background_route_sha256", payload)
        self.assertEqual(payload["firetruck_route"], self.audit.CANONICAL["firetruck_route"])
        self.assertIn("firetruck_route_sha256", payload)
        self.assertEqual(payload["stage1_dir"], self.audit.CANONICAL["stage1_dir"])
        self.assertEqual(payload["signal_pipeline_summary_json"], self.audit.CANONICAL["signal_pipeline_summary_json"])
        self.assertEqual(payload["route_geometry_recall_audit_json"], self.audit.CANONICAL["route_geometry_recall_audit_json"])
        self.assertEqual(payload["mainroad_lane_recall_audit_csv"], self.audit.CANONICAL["mainroad_lane_recall_audit_csv"])
        self.assertEqual(payload["route_internal_lane_alignment_audit_csv"], self.audit.CANONICAL["route_internal_lane_alignment_audit_csv"])
        self.assertEqual(payload["route_tls_projection_audit_csv"], self.audit.CANONICAL["route_tls_projection_audit_csv"])
        self.assertEqual(payload["decision_variables"], self.audit.CANONICAL_DECISION_VARIABLES)
        self.assertEqual(payload["optimizer_score_weights"], {"w_emv": 10.0, "w_veh": 1.0})
        self.assertEqual(payload["fixed_budget"]["n"], 15)
        self.assertEqual(payload["fixed_budget"]["m"], 50)
        self.assertEqual(payload["fixed_budget"]["workers_runbook"], 6)
        self.assertEqual(payload["fixed_budget"]["workers_code_default"], 1)

    def test_audit_passes_canonical_checks_without_legacy_warnings(self):
        report = self.audit.audit()

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["canonical_profile"], "B04_B4_S1_FORCED_OPTIMIZATION")
        failures = [item for item in report["findings"] if item["severity"] == "FAIL"]
        warnings = [item for item in report["findings"] if item["severity"] == "WARN"]
        infos = [item for item in report["findings"] if item["severity"] == "INFO"]
        self.assertEqual(failures, [])
        self.assertEqual(warnings, [])
        self.assertEqual(infos, [])
        checks = {item["check"] for item in report["findings"]}
        self.assertIn("runtime_default_net", checks)
        self.assertIn("runtime_score_weight", checks)
        self.assertIn("demand_summary_net_file", checks)
        self.assertIn("manifest_net_file_sha256", checks)
        self.assertIn("b04_manifest_active_net_sha256", checks)
        self.assertIn("baseline_latest_candidate", checks)
        self.assertIn("stage1_measurement_source_candidate", checks)
        self.assertIn("signal_pipeline_route_geometry_recall_status", checks)
        self.assertIn("route_internal_lane_alignment_audit_all_pass", checks)
        self.assertIn("route_tls_projection_audit_no_fail", checks)
        self.assertIn("canonical_net_sha256", checks)
        self.assertIn("canonical_net_firetruck_uncontrolled_priority", checks)

    def test_audit_cli_exits_zero_with_warnings_by_default(self):
        completed = subprocess.run(
            [sys.executable, str(AUDIT_PATH)],
            cwd=PROJECT_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("PASS profile=B04_B4_S1_FORCED_OPTIMIZATION", completed.stdout)
        self.assertIn("warnings=0", completed.stdout)
        self.assertIn("infos=0", completed.stdout)


if __name__ == "__main__":
    unittest.main()
