from __future__ import annotations

import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATED_DIR = PROJECT_ROOT / "01-2 Validated"
CUSTOM_PATH = VALIDATED_DIR / "custom_destination_pipeline.py"
RUNNER_PATH = PROJECT_ROOT / "02_simulation/run_b0_b1_b2_experiment.py"

if str(VALIDATED_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATED_DIR))

custom_spec = importlib.util.spec_from_file_location("custom_destination_pipeline_under_test", CUSTOM_PATH)
assert custom_spec and custom_spec.loader
custom = importlib.util.module_from_spec(custom_spec)
sys.modules[custom_spec.name] = custom
custom_spec.loader.exec_module(custom)

runner_spec = importlib.util.spec_from_file_location("custom_route_runner_under_test", RUNNER_PATH)
assert runner_spec and runner_spec.loader
runner = importlib.util.module_from_spec(runner_spec)
sys.modules[runner_spec.name] = runner
runner_spec.loader.exec_module(runner)


class CustomDestinationPipelineTest(unittest.TestCase):
    def test_apply_route_acceptance_writes_runner_ready_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            candidates = tmp / "candidates.csv"
            with candidates.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=custom.candidate_fields())
                writer.writeheader()
                writer.writerow(
                    {
                        "destination_id": "DEST_A",
                        "label_ko": "목적지 A",
                        "address": "서울 중구 테스트",
                        "lat": "37.56",
                        "lon": "126.99",
                        "target_edge_id": "edge-z",
                        "candidate_route_id": "CUSTOM_DEST_A_MAX_TOEGYE",
                        "candidate_policy": "max_toegye",
                        "route_edges": "edge-a edge-b edge-z",
                        "route_edge_count": "3",
                        "route_length_m": "123.4",
                        "length_increase_ratio": "0.1",
                        "spine_length_ratio": "0.8",
                        "max_consecutive_spine_length_m": "100.0",
                        "selection_score": "777",
                        "connected": "True",
                        "connection_reason": "",
                        "route_shape": "[]",
                    }
                )
            acceptance = tmp / "acceptance.json"
            acceptance.write_text(
                json.dumps(
                    {
                        "schema": "custom_destination_route_acceptance.v1",
                        "decisions": [
                            {
                                "destination_id": "DEST_A",
                                "decision": "accept",
                                "candidate_route_id": "CUSTOM_DEST_A_MAX_TOEGYE",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            rows, summary = custom.apply_route_acceptance(candidates, acceptance)

        self.assertEqual(summary["accepted_route_count"], 1)
        self.assertEqual(rows[0]["route_id"], "CUSTOM_DEST_A")
        self.assertEqual(rows[0]["scenario_id"], "DEST_A")
        self.assertEqual(rows[0]["target_edge_id"], "edge-z")
        self.assertEqual(rows[0]["selected_policy"], "max_toegye")
        self.assertEqual(rows[0]["route_edges"], "edge-a edge-b edge-z")

    def test_route_acceptance_requires_file(self) -> None:
        with self.assertRaises(custom.CustomDestinationError):
            custom.read_route_acceptance(Path("/tmp/does-not-exist-custom-route-acceptance.json"))


class CustomRouteRunnerTest(unittest.TestCase):
    def test_load_custom_accepted_routes_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "accepted.csv"
            with path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=custom.accepted_route_fields())
                writer.writeheader()
                writer.writerow(
                    {
                        "route_id": "CUSTOM_DEST_A",
                        "scenario_id": "DEST_A",
                        "destination_id": "DEST_A",
                        "label_ko": "목적지 A",
                        "address": "서울 중구 테스트",
                        "lat": "37.56",
                        "lon": "126.99",
                        "target_edge_id": "edge-z",
                        "selected_policy": "max_toegye",
                        "source_candidate_route_id": "CUSTOM_DEST_A_MAX_TOEGYE",
                        "route_edges": "edge-a edge-b edge-z",
                        "route_edge_count": "3",
                        "route_length_m": "123.4",
                        "spine_length_ratio": "0.8",
                        "max_consecutive_spine_length_m": "100",
                    }
                )

            routes = runner.load_custom_accepted_routes(path)

        self.assertEqual(set(routes), {"CUSTOM_DEST_A"})
        self.assertEqual(routes["CUSTOM_DEST_A"]["scenario_id"], "DEST_A")
        self.assertEqual(routes["CUSTOM_DEST_A"]["route_edges"], "edge-a edge-b edge-z")

    def test_load_custom_accepted_routes_rejects_missing_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "accepted.csv"
            path.write_text(
                "route_id,scenario_id,target_edge_id,selected_policy,route_edges\n"
                "CUSTOM_DEST_A,DEST_A,edge-z,max_toegye,\n",
                encoding="utf-8",
            )
            with self.assertRaises(runner.ExperimentError):
                runner.load_custom_accepted_routes(path)


if __name__ == "__main__":
    unittest.main()

