#!/usr/bin/env python3
"""Select final validation routes and aggregate paired B0/B2 results."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


SELECTED_FIELDS = [
    "rank",
    "route_id",
    "repeat_id",
    "emergency_depart",
    "B0_travel_time_sec",
    "B2_travel_time_sec",
    "B2_vs_B0_delta_sec",
    "B2_vs_B0_pct",
    "intervention_count",
    "green_extension_count",
    "t_change_switch_count",
    "realized_extension_sec",
    "final_status",
    "selection_reason",
]

COMPARISON_FIELDS = [
    "scope",
    "route_id",
    "pair_count",
    "B00_travel_time_sec",
    "B0_mean_sec",
    "B2_mean_sec",
    "delta_mean_sec",
    "improvement_pct_mean",
    "delta_std_sec",
    "delta_median_sec",
    "delta_ci95_low_sec",
    "delta_ci95_high_sec",
    "warning_count",
    "fail_count",
]

DEPARTURE_FIELDS = ["seed", "depart_min", "depart_max", "route_id", "repeat_id", "B0_depart", "B2_depart", "depart_match"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build final optimum validation reports.")
    parser.add_argument("--screening-results", type=Path, default=None)
    parser.add_argument("--final-results", type=Path, default=None)
    parser.add_argument("--selected-routes", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--screening-workers", type=int, default=6)
    parser.add_argument("--final-workers", type=int, default=3)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def row_float(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value in {"", None}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def sec(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def is_emergency_valid(row: dict[str, Any]) -> bool:
    return (
        parse_bool(row.get("emergency_arrived"))
        and not parse_bool(row.get("emergency_teleport"))
        and int(row.get("route_error_count") or 0) == 0
    )


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def comparison_row(scope: str, route_id: str, pairs: list[dict[str, Any]], b00_time: float | None) -> dict[str, Any]:
    deltas = [pair["delta"] for pair in pairs]
    b0_values = [pair["b0"] for pair in pairs]
    b2_values = [pair["b2"] for pair in pairs]
    improvement = [((pair["b0"] - pair["b2"]) / pair["b0"]) * 100.0 for pair in pairs if pair["b0"]]
    delta_mean = sum(deltas) / len(deltas) if deltas else None
    delta_std = sample_std(deltas)
    half_width = 1.96 * delta_std / math.sqrt(len(deltas)) if deltas else None
    warning_count = sum(1 for pair in pairs if pair["b0_status"] == "WARNING" or pair["b2_status"] == "WARNING")
    fail_count = sum(1 for pair in pairs if pair["b0_status"] == "FAIL" or pair["b2_status"] == "FAIL")
    return {
        "scope": scope,
        "route_id": route_id,
        "pair_count": len(pairs),
        "B00_travel_time_sec": sec(b00_time),
        "B0_mean_sec": sec(sum(b0_values) / len(b0_values)) if b0_values else "",
        "B2_mean_sec": sec(sum(b2_values) / len(b2_values)) if b2_values else "",
        "delta_mean_sec": sec(delta_mean),
        "improvement_pct_mean": sec(sum(improvement) / len(improvement)) if improvement else "",
        "delta_std_sec": sec(delta_std) if deltas else "",
        "delta_median_sec": sec(median(deltas)),
        "delta_ci95_low_sec": sec(delta_mean - half_width) if delta_mean is not None and half_width is not None else "",
        "delta_ci95_high_sec": sec(delta_mean + half_width) if delta_mean is not None and half_width is not None else "",
        "warning_count": warning_count,
        "fail_count": fail_count,
    }


def select_routes(screening_rows: list[dict[str, str]], limit: int) -> list[dict[str, Any]]:
    b0_by_key = {
        (row.get("route_id", ""), row.get("repeat_id", "")): row
        for row in screening_rows
        if row.get("mode") == "B0"
    }
    candidates = []
    for row in screening_rows:
        if row.get("mode") != "B2":
            continue
        key = (row.get("route_id", ""), row.get("repeat_id", ""))
        b0_row = b0_by_key.get(key)
        delta = row_float(row, "B2_vs_B0_travel_time_delta_sec")
        if b0_row is None or delta is None:
            continue
        if not is_emergency_valid(row) or not is_emergency_valid(b0_row):
            continue
        interventions = int(row.get("intervention_count") or 0)
        candidates.append(
            {
                "row": row,
                "b0": b0_row,
                "delta": delta,
                "interventions": interventions,
                "sort_key": (0 if delta < 0 else 1, delta, -interventions),
            }
        )
    selected = []
    for rank, item in enumerate(sorted(candidates, key=lambda item: item["sort_key"])[:limit], start=1):
        row = item["row"]
        b0_row = item["b0"]
        delta = item["delta"]
        selected.append(
            {
                "rank": rank,
                "route_id": row.get("route_id", ""),
                "repeat_id": row.get("repeat_id", ""),
                "emergency_depart": row.get("emergency_depart", ""),
                "B0_travel_time_sec": b0_row.get("emergency_travel_time_sec", ""),
                "B2_travel_time_sec": row.get("emergency_travel_time_sec", ""),
                "B2_vs_B0_delta_sec": sec(delta),
                "B2_vs_B0_pct": row.get("B2_vs_B0_pct", ""),
                "intervention_count": row.get("intervention_count", ""),
                "green_extension_count": row.get("green_extension_count", ""),
                "t_change_switch_count": row.get("t_change_switch_count", ""),
                "realized_extension_sec": row.get("realized_extension_sec", ""),
                "final_status": row.get("final_status", ""),
                "selection_reason": "largest valid B2 travel-time reduction with paired B0 and no emergency failure",
            }
        )
    return selected


def load_selected_ids(path: Path | None) -> list[str]:
    if path is None:
        return []
    return [row["route_id"] for row in read_csv(path) if row.get("route_id")]


def aggregate_final(rows: list[dict[str, str]], selected_ids: list[str], metadata: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    selected = set(selected_ids)
    b00_by_route: dict[str, float] = {}
    b0_by_key: dict[tuple[str, str], dict[str, str]] = {}
    b2_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        route_id = row.get("route_id", "")
        if selected and route_id not in selected:
            continue
        key = (route_id, row.get("repeat_id", ""))
        if row.get("mode") == "B00":
            travel = row_float(row, "emergency_travel_time_sec")
            if travel is not None:
                b00_by_route[route_id] = travel
        elif row.get("mode") == "B0":
            b0_by_key[key] = row
        elif row.get("mode") == "B2":
            b2_by_key[key] = row

    pairs_by_route: dict[str, list[dict[str, Any]]] = {}
    departure_rows = []
    for key, b0_row in sorted(b0_by_key.items()):
        b2_row = b2_by_key.get(key)
        if b2_row is None:
            continue
        route_id, repeat_id = key
        b0_depart = b0_row.get("emergency_depart", "")
        b2_depart = b2_row.get("emergency_depart", "")
        departure_rows.append(
            {
                "seed": metadata.get("seed", ""),
                "depart_min": metadata.get("depart_min", ""),
                "depart_max": metadata.get("depart_max", ""),
                "route_id": route_id,
                "repeat_id": repeat_id,
                "B0_depart": b0_depart,
                "B2_depart": b2_depart,
                "depart_match": b0_depart == b2_depart,
            }
        )
        if not is_emergency_valid(b0_row) or not is_emergency_valid(b2_row):
            continue
        b0_time = row_float(b0_row, "emergency_travel_time_sec")
        b2_time = row_float(b2_row, "emergency_travel_time_sec")
        if b0_time is None or b2_time is None:
            continue
        pairs_by_route.setdefault(route_id, []).append(
            {
                "b0": b0_time,
                "b2": b2_time,
                "delta": b2_time - b0_time,
                "b0_status": b0_row.get("final_status", ""),
                "b2_status": b2_row.get("final_status", ""),
            }
        )

    comparison_rows = [
        comparison_row("route", route_id, pairs, b00_by_route.get(route_id))
        for route_id, pairs in sorted(pairs_by_route.items())
    ]
    all_pairs = [pair for pairs in pairs_by_route.values() for pair in pairs]
    if all_pairs:
        comparison_rows.append(comparison_row("overall", "ALL", all_pairs, None))
    return comparison_rows, departure_rows, [{"route_id": route_id, "pair_count": len(pairs)} for route_id, pairs in sorted(pairs_by_route.items())]


def markdown_table(rows: list[dict[str, Any]], fields: list[str]) -> str:
    if not rows:
        return ""
    lines = ["| " + " | ".join(fields) + " |", "| " + " | ".join(["---"] * len(fields)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(field, "")) for field in fields) + " |")
    return "\n".join(lines)


def result_root(path: Path | None) -> Path | None:
    return path.parent if path is not None else None


def load_summary_for_results(path: Path | None) -> dict[str, Any]:
    root = result_root(path)
    if root is None:
        return {}
    return read_json_if_exists(root / "experiment_summary.json")


def load_manifest_for_results(path: Path | None) -> dict[str, Any]:
    root = result_root(path)
    if root is None:
        return {}
    return read_json_if_exists(root / "task_manifest.json")


def command_block(command: str) -> str:
    return f"```bash\n{command}\n```"


def screening_command(summary: dict[str, Any], workers: int) -> str:
    return (
        "python 05_theta_check_simulation/parameter_sim.py "
        "--modes B00 B0 B2 "
        f"--routes-csv {summary.get('routes_csv', '05_theta_check_simulation/routes/b0_valid_18_routes.csv')} "
        f"--b2-params {summary.get('b2_params', '05_theta_check_simulation/final_optimum_b2_parameter_sets.csv')} "
        f"--depart-min {summary.get('depart_min', 300.0)} "
        f"--depart-max {summary.get('depart_max', 2400.0)} "
        f"--seed {summary.get('seed', 20260531)} "
        "--repeats 1 "
        f"--workers {workers} "
        f"--output-prefix {summary.get('output_prefix', 'final_optimum_route_screening')} "
        "--resume"
    )


def final_command(summary: dict[str, Any], selected_ids: list[str], workers: int) -> str:
    return (
        "python 05_theta_check_simulation/parameter_sim.py "
        f"--routes {' '.join(selected_ids)} "
        "--modes B00 B0 B2 "
        f"--routes-csv {summary.get('routes_csv', '05_theta_check_simulation/routes/b0_valid_18_routes.csv')} "
        f"--b2-params {summary.get('b2_params', '05_theta_check_simulation/final_optimum_b2_parameter_sets.csv')} "
        f"--depart-min {summary.get('depart_min', 300.0)} "
        f"--depart-max {summary.get('depart_max', 2400.0)} "
        f"--seed {summary.get('seed', 20260531)} "
        f"--repeats {summary.get('repeats', 30)} "
        "--b00-repeats 1 --b0-repeats 30 --b2-repeats 30 "
        f"--workers {workers} "
        f"--output-prefix {summary.get('output_prefix', 'final_optimum_validation')} "
        "--resume"
    )


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    selected_rows: list[dict[str, Any]] = []
    selected_path = args.selected_routes
    screening_summary = load_summary_for_results(args.screening_results)
    final_summary = load_summary_for_results(args.final_results)
    final_manifest = load_manifest_for_results(args.final_results)

    if args.screening_results:
        selected_rows = select_routes(read_csv(args.screening_results), args.limit)
        selected_path = args.output_dir / "selected_routes.csv"
        write_csv(selected_path, selected_rows, SELECTED_FIELDS)

    report_parts = ["# Final Optimum Validation", ""]
    selected_ids = [row["route_id"] for row in selected_rows] or load_selected_ids(selected_path)
    if screening_summary or final_summary:
        output_root = result_root(args.final_results)
        report_parts.extend(
            [
                "## Run Metadata",
                "",
                markdown_table(
                    [
                        {
                            "screening_run_id": screening_summary.get("run_id", ""),
                            "final_run_id": final_summary.get("run_id", ""),
                            "final_status": final_summary.get("final_status", ""),
                            "task_count": final_summary.get("task_count", ""),
                            "completed_task_count": final_summary.get("completed_task_count", ""),
                            "seed": final_summary.get("seed", ""),
                            "depart_min": final_summary.get("depart_min", ""),
                            "depart_max": final_summary.get("depart_max", ""),
                            "b2_params": final_summary.get("b2_params", ""),
                            "mode_repeats": final_manifest.get("mode_repeats", ""),
                            "selected_routes": " ".join(selected_ids),
                            "output_path": output_root.as_posix() if output_root else "",
                        }
                    ],
                    [
                        "screening_run_id",
                        "final_run_id",
                        "final_status",
                        "task_count",
                        "completed_task_count",
                        "seed",
                        "depart_min",
                        "depart_max",
                        "b2_params",
                        "mode_repeats",
                        "selected_routes",
                        "output_path",
                    ],
                ),
                "",
                "## Commands",
                "",
                "Screening:",
                "",
                command_block(screening_command(screening_summary, args.screening_workers)),
                "",
                "Final validation resume command:",
                "",
                command_block(final_command(final_summary, selected_ids, args.final_workers)),
                "",
            ]
        )
    if selected_rows:
        report_parts.extend(
            [
                "## Selected Routes",
                "",
                markdown_table(selected_rows, ["rank", "route_id", "B2_vs_B0_delta_sec", "intervention_count", "final_status"]),
                "",
            ]
        )

    if args.final_results:
        comparison_rows, departure_rows, pair_counts = aggregate_final(read_csv(args.final_results), selected_ids, final_summary)
        write_csv(args.output_dir / "final_comparison_by_route.csv", [row for row in comparison_rows if row["scope"] == "route"], COMPARISON_FIELDS)
        write_csv(args.output_dir / "final_comparison_overall.csv", [row for row in comparison_rows if row["scope"] == "overall"], COMPARISON_FIELDS)
        write_csv(args.output_dir / "final_departure_schedule.csv", departure_rows, DEPARTURE_FIELDS)
        report_parts.extend(
            [
                "## Final Comparison",
                "",
                markdown_table(comparison_rows, ["scope", "route_id", "pair_count", "B0_mean_sec", "B2_mean_sec", "delta_mean_sec", "improvement_pct_mean", "delta_ci95_low_sec", "delta_ci95_high_sec", "warning_count", "fail_count"]),
                "",
                "## Pair Counts",
                "",
                markdown_table(pair_counts, ["route_id", "pair_count"]),
                "",
                "## Departure Schedule",
                "",
                f"Full paired departure schedule: `{(args.output_dir / 'final_departure_schedule.csv').as_posix()}`",
                "",
            ]
        )

    write_text(args.output_dir / "final_optimum_validation.md", "\n".join(report_parts).rstrip() + "\n")
    print(args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
