#!/usr/bin/env python3
"""Contract tests for the 10-1 fixed-depart measured replay workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


THIS_DIR = Path(__file__).resolve().parent
DEFAULT_PLAN = THIS_DIR / "10-1_measured_replay_plan.json"
PREPARE_SCRIPT = THIS_DIR / "prepare_measured_replay_pipeline.py"
SCENE_BUILDER = THIS_DIR / "build_measured_replay_scene.py"


def fail(failures: list[str], name: str, detail: Any = "") -> None:
    failures.append(f"{name}: {detail}" if detail else name)


def option_value(command: list[str], option: str) -> str:
    if option not in command:
        return ""
    idx = command.index(option)
    return command[idx + 1] if idx + 1 < len(command) else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Run measured replay pipeline contract tests.")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    args = parser.parse_args()

    failures: list[str] = []
    checks: list[str] = []

    def check(name: str, condition: bool, detail: Any = "") -> None:
        checks.append(name)
        if not condition:
            fail(failures, name, detail)

    check("prepare_script_exists", PREPARE_SCRIPT.is_file(), PREPARE_SCRIPT)
    check("scene_builder_exists", SCENE_BUILDER.is_file(), SCENE_BUILDER)
    check("plan_exists", args.plan.is_file(), args.plan)
    if not args.plan.is_file():
        print(f"measured replay pipeline checks: {len(checks)}")
        print("FAILED")
        for item in failures:
            print(f"- {item}")
        return 1

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    selection = plan.get("selection", {})
    commands = plan.get("commands", {})
    contract = plan.get("contract", {})
    measured = commands.get("measured_run", [])
    build_scene = commands.get("build_scene", [])
    depart_sec = float(selection.get("depart_sec", -1.0))

    check("plan_schema", plan.get("schema") == "10-1_measured_replay_plan.v1", plan.get("schema"))
    check("route_id_present", bool(plan.get("route_id")), plan)
    check("selected_repeat_matches_selection", plan.get("selected_repeat") == selection.get("repeat_id"), selection)
    check("measured_run_command_present", isinstance(measured, list) and len(measured) > 0, measured)
    check("build_scene_command_present", isinstance(build_scene, list) and len(build_scene) > 0, build_scene)

    depart_min = option_value(measured, "--depart-min")
    depart_max = option_value(measured, "--depart-max")
    check("measured_run_has_depart_min", bool(depart_min), measured)
    check("measured_run_has_depart_max", bool(depart_max), measured)
    if depart_min and depart_max:
        check(
            "measured_run_fixed_same_departure",
            abs(float(depart_min) - depart_sec) < 1e-6 and abs(float(depart_max) - depart_sec) < 1e-6,
            {"depart_sec": depart_sec, "depart_min": depart_min, "depart_max": depart_max},
        )
    check("measured_run_emits_fcd", "--emit-fcd" in measured, measured)
    check("measured_run_emits_tls_states", "--emit-tls-states" in measured, measured)
    check("measured_run_disables_adaptive_repeats", "--disable-adaptive-repeats" in measured, measured)
    check("measured_run_single_repeat", option_value(measured, "--repeats") == "1", measured)
    check("measured_run_single_worker", option_value(measured, "--workers") == "1", measured)
    check("build_scene_uses_measured_builder", str(SCENE_BUILDER) in build_scene, build_scene)
    check("build_scene_uses_plan", option_value(build_scene, "--plan") == str(args.plan.resolve()), build_scene)
    check("build_scene_repeat_is_measured_repeat_001", option_value(build_scene, "--repeat-id") == "repeat_001", build_scene)

    check("contract_same_departure_time", contract.get("same_departure_time") is True, contract)
    check("contract_same_route_id", contract.get("same_route_id") is True, contract)
    check("contract_source_of_truth_measured", contract.get("source_of_truth") == "measured FCD + tls_states.csv + signal_events.csv", contract)
    check("contract_browser_traffic_logic_forbidden", contract.get("browser_traffic_logic") == "forbidden", contract)
    check("contract_strict_winner_required", contract.get("strict_winner_required_for_final") is True, contract)
    check("contract_smoothing_is_interpolation_only", "interpolate" in str(contract.get("allowed_smoothing", "")), contract)

    if not bool(selection.get("strict_winner")):
        check("non_strict_selection_has_reasons", bool(selection.get("reasons")), selection)

    print(f"measured replay pipeline checks: {len(checks)}")
    if failures:
        print("FAILED")
        for item in failures[:80]:
            print(f"- {item}")
        if len(failures) > 80:
            print(f"... {len(failures) - 80} more failures")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
