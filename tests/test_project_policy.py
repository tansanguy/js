from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProjectPolicyTest(unittest.TestCase):
    def test_er_acc_013_policy_matches_manifest_and_route_snapshot(self) -> None:
        manifest = json.loads((PROJECT_ROOT / "configs/final_experiment_manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("excluded_routes", manifest)
        self.assertEqual(manifest["route_set"], "seoul_station")
        self.assertEqual(manifest["optimization_route"]["route_id"], "FIRE_TO_SEOUL_STATION")

        routes_path = PROJECT_ROOT / "05_theta_check_simulation/routes/b0_valid_18_routes.csv"
        with routes_path.open("r", encoding="utf-8-sig", newline="") as file:
            route_ids = {row["route_id"] for row in csv.DictReader(file)}

        self.assertEqual(len(route_ids), 18)
        self.assertNotIn("ER_ACC_013", route_ids)

        diagnosis = (PROJECT_ROOT / "docs/ER_ACC_013_diagnosis.md").read_text(encoding="utf-8")
        self.assertNotIn("의 `excluded_routes`에 `ER_ACC_013`을 유지한다", diagnosis)
        self.assertIn("별도 `excluded_routes` 필드를 두지 않는다", diagnosis)
        self.assertIn("05_theta_check_simulation/routes/b0_valid_18_routes.csv", diagnosis)

    def test_artifact_tracking_policy_is_documented_in_gitignore(self) -> None:
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("runs/", gitignore)
        self.assertIn("results/metrics/*/", gitignore)
        self.assertIn("configs/generated/*.csv", gitignore)
        self.assertIn("data_prepared/demand/*_scale_*.rou.xml", gitignore)
        self.assertIn(
            "!data_prepared/demand/background_routes_am_imputed_a17_a19_warm0p15_sustain0p05_seed002_sustained_3600.rou.xml",
            gitignore,
        )

    def test_readme_documents_config_and_artifact_roles(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("`config`: 초기 지도/수요 준비 단계", readme)
        self.assertIn("`configs`: 최종 manifest", readme)
        self.assertIn("`configs/generated`: BO가 만든 임시 추천 CSV 산출물", readme)
        self.assertIn("`runs`: SUMO 원본 로그", readme)
        self.assertIn("최종 재현 입력으로 쓰려면", readme)


if __name__ == "__main__":
    unittest.main()
