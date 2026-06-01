from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = PROJECT_ROOT / "05_theta_check_simulation/parameter_sim.py"

spec = importlib.util.spec_from_file_location("theta_parameter_sim", RUNNER_PATH)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ThetaCheckRouteSelectionTest(unittest.TestCase):
    def test_default_routes_csv_lives_inside_05_folder(self) -> None:
        self.assertEqual(
            runner.DEFAULT_ROUTES_CSV,
            PROJECT_ROOT / "05_theta_check_simulation/routes/b0_valid_18_routes.csv",
        )

    def test_default_routes_csv_is_b0_valid_18(self) -> None:
        routes = runner.load_routes(runner.DEFAULT_ROUTES_CSV, None, ["ER_ACC_013"])

        self.assertEqual(len(routes), 18)
        self.assertNotIn("ER_ACC_013", {row["route_id"] for row in routes})


class ThetaCheckDeparturesTest(unittest.TestCase):
    def test_departure_is_reproducible_for_same_seed(self) -> None:
        first = runner.deterministic_departure(20260531, "ER_ACC_001", "repeat_001", 550, 650)
        second = runner.deterministic_departure(20260531, "ER_ACC_001", "repeat_001", 550, 650)

        self.assertEqual(first, second)

    def test_departure_stays_inside_configured_window(self) -> None:
        for idx in range(1, 20):
            depart = runner.deterministic_departure(20260531, f"ER_ACC_{idx:03d}", "repeat_001", 550, 650)
            self.assertGreaterEqual(depart, 550)
            self.assertLessEqual(depart, 650)


class ThetaCheckRecoveryMetricTest(unittest.TestCase):
    def test_queue_recovery_uses_post_peak_stable_window(self) -> None:
        history = []
        for time_value in range(0, 1001, 10):
            if time_value < 700:
                queue = 2
            elif time_value < 760:
                queue = 12
            elif time_value < 840:
                queue = 4
            else:
                queue = 2
            history.append((float(time_value), queue))

        detail = runner.queue_recovery_detail(history, pass_time=700.0, emergency_depart=600.0)

        self.assertAlmostEqual(detail["recovery_sec"], 140.0)

    def test_speed_penalty_is_zero_at_or_above_recovery_floor(self) -> None:
        self.assertEqual(runner.recovery_speed_penalty_sec(15.0, 300.0), 0.0)


class ThetaCheckResumeTest(unittest.TestCase):
    def make_task(self, status_path: Path) -> dict[str, str]:
        return {
            "task_id": "B00__freeflow__repeat_001__ER_ACC_001",
            "task_status_json": str(status_path),
        }

    def test_resume_skips_completed_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "task_status.json"
            runner.write_json(
                status,
                {
                    "schema": "theta_check_task_status.v1",
                    "task_id": "B00__freeflow__repeat_001__ER_ACC_001",
                    "status": "PASS",
                    "result_row": {"final_status": "PASS"},
                },
            )
            pending = runner.tasks_to_run([self.make_task(status)], resume=True)

        self.assertEqual(pending, [])

    def test_resume_reruns_incomplete_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "task_status.json"
            runner.write_json(
                status,
                {
                    "schema": "theta_check_task_status.v1",
                    "task_id": "B00__freeflow__repeat_001__ER_ACC_001",
                    "status": "RUNNING",
                },
            )
            task = self.make_task(status)
            pending = runner.tasks_to_run([task], resume=True)

        self.assertEqual(pending, [task])


class ThetaCheckParameterTest(unittest.TestCase):
    def test_final_optimum_parameter_file_loads_selected_theta(self) -> None:
        params = runner.load_b2_parameter_sets(runner.FINAL_OPTIMUM_B2_PARAMS)

        self.assertEqual(params[0]["parameter_id"], "final_optimum_d450_a6_g51_t10")
        self.assertEqual(params[0]["D_det"], 450.0)
        self.assertEqual(params[0]["alpha"], 6.0)
        self.assertEqual(params[0]["G_ext"], 51.0)
        self.assertEqual(params[0]["T_change_sec"], 10.0)


class ThetaCheckFinalValidationTaskTest(unittest.TestCase):
    def make_args(self, tmp: Path) -> SimpleNamespace:
        return SimpleNamespace(
            modes=["B00", "B0", "B2"],
            repeats=30,
            b00_repeats=1,
            b0_repeats=30,
            b2_repeats=30,
            seed=20260531,
            depart_min=300,
            depart_max=2400,
            net=runner.DEFAULT_NET,
            background_route=runner.DEFAULT_BACKGROUND_ROUTE,
            tls_audit=runner.DEFAULT_TLS_AUDIT,
            priority_terminals=runner.DEFAULT_PRIORITY_TERMINALS,
            corridor_edges=runner.DEFAULT_CORRIDOR_EDGES,
            time_to_teleport=1200,
            collision_action="warn",
            timeout_steps=7200,
            timeout_sec=7200,
            recovery_buffer_sec=300,
            output_prefix="final_optimum_validation",
            run_root=tmp / "runs",
        )

    def test_final_validation_task_count_is_183_for_three_routes(self) -> None:
        routes = [{"route_id": f"ER_ACC_{idx:03d}"} for idx in range(1, 4)]
        params = runner.load_b2_parameter_sets(runner.FINAL_OPTIMUM_B2_PARAMS)
        with tempfile.TemporaryDirectory() as tmp:
            tasks = runner.build_tasks(self.make_args(Path(tmp)), "generated", "run", routes, params)

        self.assertEqual(len(tasks), 183)

    def test_final_validation_departures_are_paired_and_varied(self) -> None:
        routes = [{"route_id": "ER_ACC_001"}]
        params = runner.load_b2_parameter_sets(runner.FINAL_OPTIMUM_B2_PARAMS)
        with tempfile.TemporaryDirectory() as tmp:
            tasks = runner.build_tasks(self.make_args(Path(tmp)), "generated", "run", routes, params)

        b0 = {task["repeat_id"]: task["emergency_depart"] for task in tasks if task["mode"] == "B0"}
        b2 = {task["repeat_id"]: task["emergency_depart"] for task in tasks if task["mode"] == "B2"}

        self.assertEqual(b0, b2)
        self.assertEqual(len(set(b0.values())), 30)


if __name__ == "__main__":
    unittest.main()
