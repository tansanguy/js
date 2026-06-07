from __future__ import annotations

import csv
import contextlib
import io
import importlib.util
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = PROJECT_ROOT / "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("b4_optimization_s1forced", RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class B4OptimizationS1ForcedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def test_preflight_uses_current_s1forced_five_variable_schema(self):
        args = self.runner.parse_args(["--mock-eval", "--n", "1", "--m", "4", "--bo-initial", "2"])
        self.runner.validate_args(args)

        payload = self.runner.preflight(args)

        self.assertEqual(payload["bounds"]["decision_variables"], ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"])
        self.assertEqual(args.background_route.name, "background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml")
        self.assertEqual(args.stage1_dir.name, "b4_stage1_s1forced")

    def test_mock_run_writes_best_so_far_and_surrogate_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "outputs"
            run_root = Path(tmp) / "runs"
            with contextlib.redirect_stdout(io.StringIO()):
                code = self.runner.main([
                    "--mock-eval",
                    "--run-id",
                    "contract",
                    "--n",
                    "2",
                    "--m",
                    "5",
                    "--bo-initial",
                    "2",
                    "--ei-candidate-count",
                    "20",
                    "--output-dir",
                    str(output_dir),
                    "--run-root",
                    str(run_root),
                    "--skip-pareto",
                    "--skip-noise-check",
                ])

            self.assertEqual(code, 0)
            run_dir = output_dir / "contract"
            with (run_dir / "table1_best_so_far.csv").open("r", encoding="utf-8", newline="") as file:
                best_rows = list(csv.DictReader(file))
            self.assertEqual(len(best_rows), 6)
            self.assertEqual(set(best_rows[0]), {"method", "seed", "R1", "R2", "R3", "R4", "R5"})
            for row in best_rows:
                values = [float(row[f"R{index}"]) for index in range(1, 6)]
                self.assertEqual(values, sorted(values, reverse=True))

            with (run_dir / "table2_bo_surrogate.csv").open("r", encoding="utf-8", newline="") as file:
                bo_rows = list(csv.DictReader(file))
            self.assertEqual(len(bo_rows), 10)
            for field in ["observed_score", "best_so_far", "surrogate_mean", "surrogate_ci_low", "surrogate_ci_high", "acquisition"]:
                self.assertIn(field, bo_rows[0])
            self.assertTrue((run_dir / "figure1_best_so_far.png").is_file())
            self.assertTrue((run_dir / "figure2_bo_surrogate.png").is_file())

    def test_mock_run_writes_pareto_and_noise_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "outputs"
            run_root = Path(tmp) / "runs"
            with contextlib.redirect_stdout(io.StringIO()):
                code = self.runner.main([
                    "--mock-eval",
                    "--run-id",
                    "pareto_contract",
                    "--n",
                    "1",
                    "--m",
                    "4",
                    "--bo-initial",
                    "2",
                    "--ei-candidate-count",
                    "15",
                    "--output-dir",
                    str(output_dir),
                    "--run-root",
                    str(run_root),
                ])

            self.assertEqual(code, 0)
            run_dir = output_dir / "pareto_contract"
            with (run_dir / "table3_pareto.csv").open("r", encoding="utf-8", newline="") as file:
                pareto_rows = list(csv.DictReader(file))
            self.assertEqual(len(pareto_rows), 5)
            self.assertEqual(sum(row["is_knee"] == "True" for row in pareto_rows), 1)
            self.assertTrue((run_dir / "figure3_pareto.png").is_file())

            with (run_dir / "noise_check_5repeat.csv").open("r", encoding="utf-8", newline="") as file:
                noise_rows = list(csv.DictReader(file))
            self.assertEqual(len(noise_rows), 5)
            self.assertGreater(len({row["score"] for row in noise_rows}), 1)


if __name__ == "__main__":
    unittest.main()
