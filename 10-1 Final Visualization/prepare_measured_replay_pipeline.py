#!/usr/bin/env python3
"""Prepare the 10-1 single-run measured replay workflow.

The presentation scene graph is useful for a polished story, but the final
trusted visualization should come from one measured SUMO run.  This script
selects the best repeat from a completed final run, fixes the EV departure time
to that repeat, and writes the exact commands needed to re-run just that repeat
with FCD/TLS/event measurements enabled.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THIS_DIR = Path(__file__).resolve().parent
VALIDATION_SCRIPT = PROJECT_ROOT / "10 Final Destination Validation/final_destination_validation.py"
SCENE_BUILDER = THIS_DIR / "build_measured_replay_scene.py"
DEFAULT_METRICS_ROOT = PROJECT_ROOT / "results/metrics/compact_v9_final_destination_validation"
DEFAULT_THETA_ALL_EVALUATIONS = (
    PROJECT_ROOT
    / "09-1 B4 Optimization S1forced/outputs/s1forced_bo_fixed_v2_n1_m50_t6_20260608/all_evaluations.csv"
)
DEFAULT_ROUTE_ID = "FINAL_DEST_ER_ACC_006"


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def safe_float(value: Any, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pass"}


def repeat_number(value: str) -> int:
    match = re.search(r"(\d+)$", str(value))
    return int(match.group(1)) if match else 0


def repeat_text(number: int) -> str:
    return f"repeat_{number:03d}"


def sanitize_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")


@dataclass
class RepeatCandidate:
    repeat_id: str
    depart_sec: float
    score: float
    strict_winner: bool
    reasons: list[str]
    b04: dict[str, str]
    b4: dict[str, str]


def route_rows(metrics_root: Path, source_run_id: str, route_id: str) -> list[dict[str, str]]:
    route_csv = metrics_root / source_run_id / "final" / route_id / "route_runs.csv"
    if not route_csv.is_file():
        raise SystemExit(f"missing route_runs.csv: {rel(route_csv)}")
    return [row for row in read_csv(route_csv) if row.get("route_id") == route_id]


def paired_repeats(rows: list[dict[str, str]]) -> list[tuple[str, dict[str, str], dict[str, str]]]:
    by_repeat: dict[int, dict[str, dict[str, str]]] = {}
    for row in rows:
        number = repeat_number(row.get("repeat_id", ""))
        if number <= 0:
            continue
        by_repeat.setdefault(number, {})[row.get("mode", "")] = row
    pairs = []
    for number, modes in sorted(by_repeat.items()):
        if "B04" in modes and "B4" in modes:
            pairs.append((repeat_text(number), modes["B04"], modes["B4"]))
    return pairs


def score_pair(repeat_id: str, b04: dict[str, str], b4: dict[str, str]) -> RepeatCandidate:
    b04_arrived = truthy(b04.get("emergency_arrived")) and not truthy(b04.get("failed"))
    b4_arrived = truthy(b4.get("emergency_arrived")) and not truthy(b4.get("failed"))
    b04_t = safe_float(b04.get("T_actual_EMV_sec"), safe_float(b04.get("b0_T_actual_EMV_sec"), 0.0))
    b4_t = safe_float(b4.get("T_actual_EMV_sec"), safe_float(b4.get("b4_T_actual_EMV_sec"), 0.0))
    improvement = b04_t - b4_t if b04_t > 0.0 and b4_t > 0.0 else 0.0
    stage2 = safe_float(b4.get("stage2_hold_count"))
    stage3 = safe_float(b4.get("stage3_preemption_count"))
    signal_events = safe_float(b4.get("signal_event_count"))
    background_ratio = safe_float(b4.get("background_arrived_ratio"))
    queue_trigger = safe_float(b4.get("queue_trigger_count"))

    reasons: list[str] = []
    score = improvement
    if b04_arrived:
        score += 40.0
    else:
        score -= 120.0
        reasons.append("B04_not_arrived_or_failed")
    if b4_arrived:
        score += 120.0
    else:
        score -= 240.0
        reasons.append("B4_not_arrived_or_failed")
    if improvement > 0:
        score += min(improvement, 240.0) * 0.35
    else:
        score -= 120.0
        reasons.append("B4_not_faster_than_B04")
    if stage2 > 0:
        score += min(stage2, 3.0) * 12.0
    else:
        reasons.append("stage2_not_visible")
    if stage3 > 0:
        score += min(stage3, 24.0) * 4.0
    else:
        reasons.append("stage3_not_visible")
    if signal_events > 0:
        score += min(signal_events, 80.0) * 0.4
    else:
        reasons.append("no_signal_events")
    score += min(queue_trigger, 120.0) * 0.05
    score += min(background_ratio, 1.0) * 10.0

    strict_winner = b04_arrived and b4_arrived and improvement > 0 and stage3 > 0 and signal_events > 0
    if strict_winner:
        reasons.append("strict_winner")
    depart = safe_float(b04.get("emergency_depart"), safe_float(b4.get("emergency_depart"), 600.0))
    return RepeatCandidate(repeat_id, depart, round(score, 6), strict_winner, reasons, b04, b4)


def select_repeat(rows: list[dict[str, str]]) -> RepeatCandidate:
    candidates = [score_pair(repeat_id, b04, b4) for repeat_id, b04, b4 in paired_repeats(rows)]
    if not candidates:
        raise SystemExit("no paired B04/B4 repeats found")
    strict = [item for item in candidates if item.strict_winner]
    pool = strict or candidates
    return sorted(pool, key=lambda item: (item.score, item.depart_sec), reverse=True)[0]


def metric_snapshot(row: dict[str, str]) -> dict[str, Any]:
    keys = [
        "mode",
        "repeat_id",
        "emergency_depart",
        "final_status",
        "failed",
        "failure_reason",
        "emergency_arrived",
        "T_actual_EMV_sec",
        "D_E_sec",
        "D_G_sec",
        "signal_event_count",
        "stage2_hold_count",
        "stage3_preemption_count",
        "stage3_restore_count",
        "background_departed",
        "background_arrived",
        "background_arrived_ratio",
        "signal_events_csv",
    ]
    return {key: row.get(key, "") for key in keys}


def build_validation_command(args: argparse.Namespace, measured_run_id: str, depart_sec: float) -> list[str]:
    return [
        sys.executable,
        str(VALIDATION_SCRIPT),
        "--phase",
        "final",
        "--run-id",
        measured_run_id,
        "--theta-all-evaluations",
        str(args.theta_all_evaluations.resolve()),
        "--theta-method",
        args.theta_method,
        "--selected-routes",
        args.route_id,
        "--final-selection-count",
        "1",
        "--repeats",
        "1",
        "--workers",
        str(args.workers),
        "--depart-min",
        f"{depart_sec:.3f}",
        "--depart-max",
        f"{depart_sec:.3f}",
        "--emit-fcd",
        "--emit-tls-states",
        "--disable-adaptive-repeats",
    ]


def build_scene_command(args: argparse.Namespace, measured_run_id: str, plan_path: Path) -> list[str]:
    output = THIS_DIR / "seoul_station_fire_station_measured_replay.html"
    return [
        sys.executable,
        str(SCENE_BUILDER),
        "--plan",
        str(plan_path),
        "--run-id",
        measured_run_id,
        "--route-id",
        args.route_id,
        "--repeat-id",
        "repeat_001",
        "--output",
        str(output),
    ]


def run_command(cmd: list[str]) -> None:
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select and prepare one measured replay run for 10-1.")
    parser.add_argument("--source-run-id", required=True, help="Completed final run to inspect.")
    parser.add_argument("--route-id", default=DEFAULT_ROUTE_ID)
    parser.add_argument("--metrics-root", type=Path, default=DEFAULT_METRICS_ROOT)
    parser.add_argument("--theta-all-evaluations", type=Path, default=DEFAULT_THETA_ALL_EVALUATIONS)
    parser.add_argument("--theta-method", default="BO", choices=["ALL", "BO", "CMA-ES", "Random Search"])
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--measured-run-id", default="")
    parser.add_argument("--output", type=Path, default=THIS_DIR / "10-1_measured_replay_plan.json")
    parser.add_argument("--execute-measured-run", action="store_true")
    parser.add_argument("--build-scene", action="store_true")
    parser.add_argument(
        "--allow-non-strict",
        action="store_true",
        help="Allow debug execution even when the selected repeat is not a strict B4 winner.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = route_rows(args.metrics_root.resolve(), args.source_run_id, args.route_id)
    selected = select_repeat(rows)
    measured_run_id = args.measured_run_id or (
        f"10_1_measured_{sanitize_token(args.source_run_id)}_{sanitize_token(args.route_id)}_{selected.repeat_id}"
    )
    output = args.output.resolve()
    validation_command = build_validation_command(args, measured_run_id, selected.depart_sec)
    scene_command = build_scene_command(args, measured_run_id, output)
    plan = {
        "schema": "10-1_measured_replay_plan.v1",
        "objective": "single effective final run -> fixed-depart measured rerun -> raw replay visualization",
        "source_run_id": args.source_run_id,
        "measured_run_id": measured_run_id,
        "route_id": args.route_id,
        "selected_repeat": selected.repeat_id,
        "selection": {
            "repeat_id": selected.repeat_id,
            "depart_sec": selected.depart_sec,
            "score": selected.score,
            "strict_winner": selected.strict_winner,
            "reasons": selected.reasons,
            "B04": metric_snapshot(selected.b04),
            "B4": metric_snapshot(selected.b4),
        },
        "commands": {
            "measured_run": validation_command,
            "build_scene": scene_command,
        },
        "outputs": {
            "plan": rel(output),
            "measured_html": rel(THIS_DIR / "seoul_station_fire_station_measured_replay.html"),
            "measured_data": rel(THIS_DIR / "seoul_station_fire_station_measured_replay_data.json"),
            "measured_manifest": rel(THIS_DIR / "seoul_station_fire_station_measured_replay_manifest.json"),
        },
        "contract": {
            "same_departure_time": True,
            "same_route_id": True,
            "source_of_truth": "measured FCD + tls_states.csv + signal_events.csv",
            "browser_traffic_logic": "forbidden",
            "allowed_smoothing": "interpolate between measured FCD samples only",
            "strict_winner_required_for_final": True,
        },
    }
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output}")
    print(json.dumps(plan["selection"], ensure_ascii=False, indent=2))
    if not selected.strict_winner:
        print(
            "Selected repeat is not a strict B4 winner; use this plan for wiring checks only.",
            file=sys.stderr,
        )
        if (args.execute_measured_run or args.build_scene) and not args.allow_non_strict:
            raise SystemExit("refusing to execute non-strict measured replay without --allow-non-strict")
    if args.execute_measured_run:
        run_command(validation_command)
    if args.build_scene:
        run_command(scene_command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
