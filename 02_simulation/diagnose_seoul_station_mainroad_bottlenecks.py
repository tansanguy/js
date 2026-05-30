#!/usr/bin/env python3
"""Diagnose bottlenecks on the fixed Seoul Station route from experiment outputs."""

from __future__ import annotations

import argparse
import csv
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_CSV = PROJECT_ROOT / "results/metrics/seoul_station_straight_final_smoke/latest.json"
DEFAULT_OUTPUT_CSV = PROJECT_ROOT / "results/metrics/seoul_station_straight_bottleneck_diagnosis.csv"
DEFAULT_OUTPUT_JSON = PROJECT_ROOT / "results/metrics/seoul_station_straight_bottleneck_diagnosis.json"
DEFAULT_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger_speed50.net.xml"

WATCHED_TRANSITIONS = [
    ("347237859#3", "347237859#4"),
    ("347237859#4", "781985787#0"),
    ("781985787#0", "218915135#3"),
    ("218915135#3", "218915135#4"),
    ("218915135#4", "781983104#0"),
    ("781983104#1", "333557072#3"),
    ("333557072#5", "619147738#0"),
]


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def resolve_results_csv(path: Path) -> Path:
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        csv_path = resolve_path(str(data.get("results_csv", "")))
        if not csv_path.is_file():
            raise SystemExit(f"missing results csv from latest json: {csv_path}")
        return csv_path
    return path


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_lane_connection_data(net_path: Path) -> tuple[dict[str, int], dict[tuple[str, str], set[int]]]:
    lane_counts: dict[str, int] = {}
    transition_lanes: dict[tuple[str, str], set[int]] = {}
    root = ET.parse(net_path).getroot()
    for edge in root.findall("edge"):
        edge_id = edge.get("id", "")
        if not edge_id or edge_id.startswith(":") or edge.get("function") == "internal":
            continue
        lanes = edge.findall("lane")
        if lanes:
            lane_counts[edge_id] = len(lanes)
    for connection in root.findall("connection"):
        from_edge = connection.get("from", "")
        to_edge = connection.get("to", "")
        from_lane = connection.get("fromLane", "")
        if from_edge in lane_counts and to_edge in lane_counts and from_lane.isdigit():
            transition_lanes.setdefault((from_edge, to_edge), set()).add(int(from_lane))
    return lane_counts, transition_lanes


def parse_edge_data(path: Path) -> dict[str, dict[str, float]]:
    if not path.is_file():
        return {}
    root = ET.parse(path).getroot()
    rows: dict[str, dict[str, float]] = {}
    for edge in root.findall(".//edge"):
        edge_id = edge.get("id", "")
        if not edge_id:
            continue
        rows[edge_id] = {
            "speed_kmh": float(edge.get("speed") or 0.0) * 3.6,
            "traveltime": float(edge.get("traveltime") or 0.0),
            "waiting_time": float(edge.get("waitingTime") or 0.0),
            "time_loss": float(edge.get("timeLoss") or 0.0),
            "entered": float(edge.get("entered") or 0.0),
            "left": float(edge.get("left") or 0.0),
            "lane_changed_from": float(edge.get("laneChangedFrom") or 0.0),
            "lane_changed_to": float(edge.get("laneChangedTo") or 0.0),
            "sampled_seconds": float(edge.get("sampledSeconds") or 0.0),
            "occupancy": float(edge.get("occupancy") or 0.0),
        }
    return rows


def parse_stderr(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    edge_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    lines_by_edge: dict[str, list[str]] = {}
    for line in text.splitlines():
        if "Warning:" not in line and "Error:" not in line:
            continue
        edge_candidates = re.findall(r"(?:edge|lane)='([^'_]+(?:#[^'_]+)?)", line)
        for edge_id in edge_candidates:
            edge_counts[edge_id] += 1
            lines_by_edge.setdefault(edge_id, []).append(line)
        lowered = line.lower()
        if "wrong lane" in lowered:
            reason_counts["wrong_lane"] += 1
        elif "jam" in lowered:
            reason_counts["jam"] += 1
        elif "yield" in lowered:
            reason_counts["yield"] += 1
        elif "teleport" in lowered:
            reason_counts["teleport"] += 1
        else:
            reason_counts["other"] += 1
    return {
        "warning_count": sum(edge_counts.values()),
        "edge_counts": dict(edge_counts),
        "reason_counts": dict(reason_counts),
        "lines_by_edge": lines_by_edge,
    }


def classify_transition(from_values: dict[str, float], to_values: dict[str, float], lost_lanes: int, stderr_reasons: dict[str, int]) -> str:
    min_speed = min(from_values.get("speed_kmh", 999.0), to_values.get("speed_kmh", 999.0))
    total_wait = from_values.get("waiting_time", 0.0) + to_values.get("waiting_time", 0.0)
    net_unserved = (
        from_values.get("entered", 0.0)
        - from_values.get("left", 0.0)
        + to_values.get("entered", 0.0)
        - to_values.get("left", 0.0)
    )
    if stderr_reasons.get("wrong_lane", 0) > 0:
        return "lane_selection_conflict"
    if min_speed < 2.0 and total_wait > 1000.0 and lost_lanes >= 2:
        return "severe_lane_drop_queue"
    if min_speed < 5.0 and total_wait > 500.0:
        return "severe_queue"
    if net_unserved > 10:
        return "residual_queue"
    if lost_lanes > 0:
        return "lane_drop_watch"
    return "no_clear_bottleneck"


def transition_rows(results: list[dict[str, str]], net_path: Path) -> list[dict[str, Any]]:
    lane_counts, transition_lanes = load_lane_connection_data(net_path)
    rows: list[dict[str, Any]] = []
    for result in results:
        mode = result.get("mode", "")
        edge_data = parse_edge_data(resolve_path(result.get("edgeData_output", ""))) if result.get("edgeData_output") else {}
        stderr = parse_stderr(resolve_path(result.get("stderr_log", ""))) if result.get("stderr_log") else {"edge_counts": {}, "reason_counts": {}}
        route_edges = result.get("route_id", "")
        for index, (from_edge, to_edge) in enumerate(WATCHED_TRANSITIONS, start=1):
            from_values = edge_data.get(from_edge, {})
            to_values = edge_data.get(to_edge, {})
            from_lanes = lane_counts.get(from_edge, 1)
            connected_lanes = len(transition_lanes.get((from_edge, to_edge), set()))
            lost_lanes = max(from_lanes - connected_lanes, 0)
            edge_warning_count = int(stderr.get("edge_counts", {}).get(from_edge, 0)) + int(stderr.get("edge_counts", {}).get(to_edge, 0))
            reason_counts = stderr.get("reason_counts", {})
            rows.append(
                {
                    "mode": mode,
                    "route_id": route_edges,
                    "transition_index": index,
                    "from_edge": from_edge,
                    "to_edge": to_edge,
                    "from_lanes": from_lanes,
                    "connected_lanes": connected_lanes,
                    "lost_lanes": lost_lanes,
                    "from_speed_kmh": round(from_values.get("speed_kmh", 0.0), 3),
                    "to_speed_kmh": round(to_values.get("speed_kmh", 0.0), 3),
                    "from_waiting_time_sec": round(from_values.get("waiting_time", 0.0), 3),
                    "to_waiting_time_sec": round(to_values.get("waiting_time", 0.0), 3),
                    "from_entered": round(from_values.get("entered", 0.0), 3),
                    "from_left": round(from_values.get("left", 0.0), 3),
                    "to_entered": round(to_values.get("entered", 0.0), 3),
                    "to_left": round(to_values.get("left", 0.0), 3),
                    "from_lane_changes": round(from_values.get("lane_changed_from", 0.0), 3),
                    "to_lane_changes": round(to_values.get("lane_changed_to", 0.0), 3),
                    "stderr_warning_count_for_edges": edge_warning_count,
                    "stderr_reason_counts": json.dumps(reason_counts, ensure_ascii=False, sort_keys=True),
                    "emergency_arrived": result.get("emergency_arrived", ""),
                    "emergency_teleport": result.get("emergency_teleport", ""),
                    "emergency_travel_time_sec": result.get("emergency_travel_time_sec") or result.get("emergency_travel_time", ""),
                    "diagnosis": classify_transition(from_values, to_values, lost_lanes, reason_counts),
                    "edgeData_output": result.get("edgeData_output", ""),
                    "stderr_log": result.get("stderr_log", ""),
                }
            )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-csv", type=Path, default=DEFAULT_RESULTS_CSV)
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.results_csv.is_file():
        raise SystemExit(f"missing results csv/latest json: {args.results_csv}")
    results_csv = resolve_results_csv(args.results_csv)
    results = read_csv(results_csv)
    rows = transition_rows(results, args.net)
    write_csv(args.output_csv, rows)
    by_mode: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_mode.setdefault(str(row["mode"]), []).append(row)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results_csv": rel(results_csv),
        "net": rel(args.net),
        "watched_transitions": [{"from_edge": a, "to_edge": b} for a, b in WATCHED_TRANSITIONS],
        "row_count": len(rows),
        "modes": {
            mode: {
                "diagnosis_counts": dict(Counter(str(row["diagnosis"]) for row in mode_rows)),
                "max_waiting_time_sec": max(
                    (float(row["from_waiting_time_sec"]) + float(row["to_waiting_time_sec"]) for row in mode_rows),
                    default=0.0,
                ),
                "min_transition_speed_kmh": min(
                    (min(float(row["from_speed_kmh"]), float(row["to_speed_kmh"])) for row in mode_rows),
                    default=0.0,
                ),
            }
            for mode, mode_rows in by_mode.items()
        },
        "outputs": {"csv": rel(args.output_csv), "json": rel(args.output_json)},
    }
    write_json(args.output_json, payload)
    print(f"wrote {args.output_csv}")
    print(f"wrote {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
