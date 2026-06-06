#!/usr/bin/env python3
"""Post-process the u130 Init20 B4 search outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = Path(__file__).resolve().parent
TDATA_ROOT = PIPELINE_DIR / "tdata_signal"
DEFAULT_OUTPUT_PREFIX = "compact_v9_B4_theta_u130_init20"
DEFAULT_METRICS_ROOT = PROJECT_ROOT / "results/metrics" / DEFAULT_OUTPUT_PREFIX
DEFAULT_RUN_ROOT = PROJECT_ROOT / "runs" / DEFAULT_OUTPUT_PREFIX
STAGE1_CSV = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1/b4_approach_storage_link_plan.csv"
ACTIVE_DEMAND = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml"
ACTIVE_NET = TDATA_ROOT / "nets/jungbu_compact_v9_B04_global_reality_mild.net.xml"
B04_VALIDATION_CSV = PROJECT_ROOT / "results/metrics/compact_v9_B4_target15_demand/u130_B04/experiment_results.csv"
B4_VALIDATION_CSV = PROJECT_ROOT / "results/metrics/compact_v9_B4_target15_demand/u130_tau085/experiment_results.csv"
FOCUS_SEGMENTS = {"S6:upbound", "S7:upbound", "S9:upbound", "S15:upbound"}
PREFLIGHT_WARNINGS = [
    "SUMO load passed, but SUMO_HOME is not set so XML validation was disabled.",
    "Unused TLS state warnings were observed for 8487751470, COMPACT_V9_FIRE_STATION_ENTRY_TLS, cluster_11277565406_11277565407_11277565408_11277565409_#6more, cluster_11347624895_11414286294, and joinedS_11414286323_cluster_11414286309_7666741014.",
    "COMPACT_V9_FIRE_STATION_ENTRY_TLS has an unsafe green warning on lane -174870621#8_0; this did not block SUMO execution but should stay in the net cleanup backlog.",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def latest_run_id(metrics_root: Path) -> str:
    latest = metrics_root / "latest.json"
    if latest.is_file():
        payload = json.loads(latest.read_text(encoding="utf-8"))
        if payload.get("run_id"):
            return str(payload["run_id"])
    candidates = sorted(path.name for path in metrics_root.iterdir() if path.is_dir()) if metrics_root.is_dir() else []
    if not candidates:
        raise FileNotFoundError(f"no_run_id_found:{metrics_root}")
    return candidates[-1]


def stage1_movements() -> list[dict[str, Any]]:
    rows = read_csv(STAGE1_CSV)
    movements: list[dict[str, Any]] = []
    for row in rows:
        movements.append({
            "movement_id": row.get("movement_id", ""),
            "segment_id": row.get("mapped_S_segment", ""),
            "route_order_index": safe_float(row.get("route_order_index")),
            "tls_id": row.get("tls_id", ""),
            "edges": [edge for edge in row.get("corridor_storage_edges", "").split() if edge],
        })
    return movements


def candidate_edge_metrics(edge_data: Path, movements: list[dict[str, Any]], begin_min: float = 600.0) -> dict[str, dict[str, Any]]:
    edge_to_movement_ids: dict[str, list[str]] = defaultdict(list)
    for movement in movements:
        for edge in movement["edges"]:
            edge_to_movement_ids[edge].append(movement["movement_id"])
    acc: dict[str, dict[str, Any]] = {
        movement["movement_id"]: {
            "sampled": 0.0,
            "speed_weighted": 0.0,
            "low10": 0.0,
            "density_weighted": 0.0,
            "waiting": 0.0,
            "time_loss": 0.0,
            "entered": 0,
            "left": 0,
        }
        for movement in movements
    }
    if not edge_data.is_file():
        return acc
    current_begin = 0.0
    for event, elem in ET.iterparse(edge_data, events=("start", "end")):
        if event == "start" and elem.tag == "interval":
            current_begin = safe_float(elem.get("begin"))
        elif event == "end" and elem.tag == "edge":
            if current_begin < begin_min:
                elem.clear()
                continue
            edge_id = elem.get("id", "")
            movement_ids = edge_to_movement_ids.get(edge_id, [])
            if not movement_ids:
                elem.clear()
                continue
            sampled = safe_float(elem.get("sampledSeconds"))
            if sampled <= 0:
                elem.clear()
                continue
            speed_kmh = safe_float(elem.get("speed")) * 3.6
            density = safe_float(elem.get("density"))
            for movement_id in movement_ids:
                item = acc[movement_id]
                item["sampled"] += sampled
                item["speed_weighted"] += speed_kmh * sampled
                item["low10"] += sampled if speed_kmh < 10.0 else 0.0
                item["density_weighted"] += density * sampled
                item["waiting"] += safe_float(elem.get("waitingTime"))
                item["time_loss"] += safe_float(elem.get("timeLoss"))
                item["entered"] += int(safe_float(elem.get("entered")))
                item["left"] += int(safe_float(elem.get("left")))
            elem.clear()
    result: dict[str, dict[str, Any]] = {}
    for movement_id, item in acc.items():
        sampled = safe_float(item["sampled"])
        result[movement_id] = {
            "speed_kmh": round(item["speed_weighted"] / sampled, 3) if sampled else "",
            "low_lt10_ratio": round(item["low10"] / sampled, 4) if sampled else "",
            "density": round(item["density_weighted"] / sampled, 3) if sampled else "",
            "waiting_sec": round(item["waiting"], 3),
            "time_loss_sec": round(item["time_loss"], 3),
            "entered": item["entered"],
            "left": item["left"],
            "sampled_seconds": round(sampled, 3),
        }
    return result


def percentile(values: list[float], ratio: float) -> Any:
    if not values:
        return ""
    values = sorted(values)
    index = min(len(values) - 1, max(0, round((len(values) - 1) * ratio)))
    return round(values[index], 4)


def tau_event_rows(signal_events: Path) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"raw": [], "effective": [], "scale": []})
    if not signal_events.is_file():
        return []
    for row in read_csv(signal_events):
        segment_id = row.get("original_tau_segment_id", "")
        if not segment_id:
            continue
        raw = safe_float(row.get("original_tau_raw_fill"), safe_float(row.get("original_tau_fill"), math.nan))
        effective = safe_float(row.get("original_tau_effective_fill"), safe_float(row.get("original_tau_fill"), math.nan))
        scale = safe_float(row.get("original_tau_scale"), 1.0)
        if math.isnan(raw) or math.isnan(effective):
            continue
        grouped[segment_id]["raw"].append(raw)
        grouped[segment_id]["effective"].append(effective)
        grouped[segment_id]["scale"].append(scale)
    rows: list[dict[str, Any]] = []
    for segment_id, values in sorted(grouped.items()):
        effective = values["effective"]
        raw = values["raw"]
        rows.append({
            "segment_id": segment_id,
            "samples": len(effective),
            "raw_mean": round(sum(raw) / len(raw), 4) if raw else "",
            "effective_mean": round(sum(effective) / len(effective), 4) if effective else "",
            "effective_p50": percentile(effective, 0.50),
            "effective_p85": percentile(effective, 0.85),
            "effective_p95": percentile(effective, 0.95),
            "effective_max": round(max(effective), 4) if effective else "",
            "tau_scale": round(sum(values["scale"]) / len(values["scale"]), 4) if values["scale"] else "",
            "hit_0p65": sum(value >= 0.65 for value in effective),
            "hit_0p75": sum(value >= 0.75 for value in effective),
            "hit_0p85": sum(value >= 0.85 for value in effective),
        })
    return rows


def row_speed_kmh(row: dict[str, str]) -> float:
    return safe_float(row.get("route_length_m")) / safe_float(row.get("T_actual_EMV_sec"), 1.0) * 3.6


def archive_manifest() -> dict[str, Any]:
    variants = ["u120", "u140", "u150", "u160", "s125", "s135", "s145", "s160", "s175"]
    demand_dir = PROJECT_ROOT / "data_prepared/compact_v9/demand"
    metrics_root = PROJECT_ROOT / "results/metrics/compact_v9_B4_target15_demand"
    run_root = PROJECT_ROOT / "runs/compact_v9_B4_target15_demand"
    entries = []
    for variant in variants:
        run_id = f"target15_{variant}_tau085_20260605"
        entries.append({
            "variant": variant,
            "reason": "failed_or_non_active_target15_variant",
            "demand_file": rel(demand_dir / f"background_routes_compact_v9_B04_target15_{variant}.rou.xml"),
            "demand_summary": rel(demand_dir / f"background_routes_compact_v9_B04_target15_{variant}.rou.summary.json"),
            "metrics_dir": rel(metrics_root / f"{variant}_tau085"),
            "run_dir": rel(run_root / run_id),
            "active_replacement": rel(ACTIVE_DEMAND),
            "archive_policy": "manifest_quarantine_files_left_in_place",
        })
    return {
        "schema": "compact_v9_target15_archive_manifest.v1",
        "generated_at": utc_now(),
        "active_keep": {
            "demand": rel(ACTIVE_DEMAND),
            "net": rel(ACTIVE_NET),
            "stage1_dir": rel(PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1"),
            "b04_validation": rel(B04_VALIDATION_CSV),
            "b4_validation": rel(B4_VALIDATION_CSV),
        },
        "entries": entries,
    }


def candidate_status_counts(top20: list[dict[str, str]]) -> dict[str, int]:
    return {
        "total": len(top20),
        "pass": sum(row.get("final_status") == "PASS" for row in top20),
        "fail": sum(row.get("final_status") != "PASS" for row in top20),
        "stuck": sum(row.get("failure_reason") == "emergency_stuck" for row in top20),
    }


def best_candidate_focus_segments(top20: list[dict[str, str]], segment_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_pass = next((row.get("parameter_id", "") for row in top20 if row.get("final_status") == "PASS"), "")
    if not best_pass:
        return []
    rows = [
        row
        for row in segment_rows
        if row.get("parameter_id") == best_pass and row.get("segment_id") in FOCUS_SEGMENTS
    ]
    return sorted(rows, key=lambda row: (row.get("segment_id", ""), safe_float(row.get("route_order_index"))))


def best_candidate_tau_focus(top20: list[dict[str, str]], tau_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_pass = next((row.get("parameter_id", "") for row in top20 if row.get("final_status") == "PASS"), "")
    if not best_pass:
        return []
    rows = [
        row
        for row in tau_rows
        if row.get("parameter_id") == best_pass and row.get("segment_id") in FOCUS_SEGMENTS
    ]
    return sorted(rows, key=lambda row: row.get("segment_id", ""))


def write_review_doc(path: Path, payload: dict[str, Any]) -> None:
    b04 = payload["b04_validation"]
    b4 = payload["b4_validation"]
    top = payload["top20"][:5]
    counts = payload["candidate_counts"]
    lines = [
        "# B04/B4 Algorithm Review and Init20 Validation",
        "",
        f"- generated_at: {utc_now()}",
        f"- active demand: `{rel(ACTIVE_DEMAND)}`",
        f"- active net: `{rel(ACTIVE_NET)}`",
        f"- tau_scale: `{payload['tau_scale']}`",
        "",
        "## Validation Gates",
        "",
        f"- B04 u130: status={b04.get('final_status')}, EV={b04.get('T_actual_EMV_sec')} sec, speed={payload['b04_ev_speed_kmh']} km/h, teleport={b04.get('emergency_teleport')}",
        f"- B4 u130 tau085 smoke: status={b4.get('final_status')}, EV={b4.get('T_actual_EMV_sec')} sec, teleport={b4.get('emergency_teleport')}",
        f"- Init20: {counts['pass']} PASS / {counts['fail']} FAIL; emergency_stuck={counts['stuck']}",
        "",
        "## Preflight Warnings",
        "",
        *[f"- {warning}" for warning in payload["preflight_warnings"]],
        "",
        "## Algorithm Review",
        "",
        "- B04 is kept as no-control baseline only; its role is validating demand/signal plausibility.",
        "- B4 controls the EV-route main-road Stage1 movement set, currently 17 controllable movements.",
        "- Stage2 handles departure/merge hold; Stage3 uses route-order original Tau, TA proxy, phase restore, and expiry controls.",
        "- Original Tau trigger uses effective Tau: `min(raw_tau * tau_scale, 1.0)`, while raw/effective/scale are logged in signal events.",
        "- Candidate score is `10 * T_actual_EMV_sec + 1 * general_mean_travel_time_sec`; stuck/teleport/fail receive BO penalty.",
        "",
        "## Top Candidates",
        "",
    ]
    for row in top:
        lines.append(
            f"- rank {row.get('rank')}: {row.get('parameter_id')} "
            f"score={row.get('bo_score_sec')} EV={row.get('T_actual_EMV_sec')} "
            f"general={row.get('general_mean_travel_time_sec')} tau={row.get('tau')} "
            f"ext={row.get('ext_max')} hold={row.get('hold_max')} d_up={row.get('d_up')}"
        )
    lines.extend([
        "",
        "## Remaining Bottlenecks",
        "",
        "- The best ranked candidate improves EV travel time, but corridor bottlenecks remain in the focus S segments.",
    ])
    for row in payload["best_candidate_focus_segments"]:
        lines.append(
            f"- {row.get('movement_id')} {row.get('segment_id')}: "
            f"speed={row.get('speed_kmh')} km/h, low_lt10={row.get('low_lt10_ratio')}, "
            f"waiting={row.get('waiting_sec')} sec"
        )
    lines.extend([
        "",
        "## Tau Effective Fill",
        "",
        "- Tau trigger uses effective fill, but S9 still often saturates at the scaled ceiling under u130 demand.",
    ])
    for row in payload["best_candidate_tau_focus"]:
        lines.append(
            f"- {row.get('segment_id')}: samples={row.get('samples')}, "
            f"raw_mean={row.get('raw_mean')}, effective_p95={row.get('effective_p95')}, "
            f"hit_0p85={row.get('hit_0p85')}"
        )
    lines.extend([
        "",
        "## Outputs",
        "",
        f"- top20: `{payload['top20_csv']}`",
        f"- segment summary: `{payload['segment_csv']}`",
        f"- Tau summary: `{payload['tau_csv']}`",
        f"- archive manifest: `{payload['archive_manifest_json']}`",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = args.run_id or latest_run_id(args.metrics_root)
    run_metrics_dir = args.metrics_root / run_id
    out_dir = args.output_dir or (TDATA_ROOT / "u130_init20_review")
    all_values = read_csv(run_metrics_dir / "bo_all_values.csv")
    top20 = read_csv(run_metrics_dir / "top20_ranked.csv")
    movements = stage1_movements()
    movement_by_id = {movement["movement_id"]: movement for movement in movements}

    segment_rows: list[dict[str, Any]] = []
    tau_rows: list[dict[str, Any]] = []
    for row in all_values:
        if row.get("mode") != "B4":
            continue
        parameter_id = row.get("parameter_id", "")
        candidate_dir = args.run_root / run_id / "B4" / parameter_id / "repeat_001"
        edge_metrics = candidate_edge_metrics(candidate_dir / "edgeData.xml", movements)
        for movement_id, metrics in edge_metrics.items():
            movement = movement_by_id[movement_id]
            segment_rows.append({
                "parameter_id": parameter_id,
                "final_status": row.get("final_status", ""),
                "bo_score_sec": row.get("bo_score_sec", ""),
                "movement_id": movement_id,
                "segment_id": movement["segment_id"],
                "route_order_index": int(movement["route_order_index"]),
                "speed_kmh": metrics.get("speed_kmh", ""),
                "low_lt10_ratio": metrics.get("low_lt10_ratio", ""),
                "density": metrics.get("density", ""),
                "waiting_sec": metrics.get("waiting_sec", ""),
                "time_loss_sec": metrics.get("time_loss_sec", ""),
                "entered": metrics.get("entered", ""),
                "left": metrics.get("left", ""),
            })
        for tau_row in tau_event_rows(candidate_dir / "signal_events.csv"):
            tau_rows.append({
                "parameter_id": parameter_id,
                "final_status": row.get("final_status", ""),
                "bo_score_sec": row.get("bo_score_sec", ""),
                **tau_row,
            })

    segment_csv = out_dir / "init20_candidate_segment_speed_summary.csv"
    tau_csv = out_dir / "init20_candidate_tau_event_summary.csv"
    archive_json = out_dir / "target15_archive_manifest.json"
    review_md = out_dir / "algorithm_review_and_validation.md"
    write_csv(segment_csv, segment_rows, [
        "parameter_id",
        "final_status",
        "bo_score_sec",
        "movement_id",
        "segment_id",
        "route_order_index",
        "speed_kmh",
        "low_lt10_ratio",
        "density",
        "waiting_sec",
        "time_loss_sec",
        "entered",
        "left",
    ])
    write_csv(tau_csv, tau_rows, [
        "parameter_id",
        "final_status",
        "bo_score_sec",
        "segment_id",
        "samples",
        "raw_mean",
        "effective_mean",
        "effective_p50",
        "effective_p85",
        "effective_p95",
        "effective_max",
        "tau_scale",
        "hit_0p65",
        "hit_0p75",
        "hit_0p85",
    ])
    archive = archive_manifest()
    write_json(archive_json, archive)

    b04_rows = read_csv(B04_VALIDATION_CSV)
    b4_rows = read_csv(B4_VALIDATION_CSV)
    b04 = b04_rows[0] if b04_rows else {}
    b4 = b4_rows[0] if b4_rows else {}
    b04_ev_speed = safe_float(b04.get("EV_avg_speed_including_stops_kmh"))
    if not b04_ev_speed and b04.get("T_actual_EMV_sec"):
        b04_ev_speed = 4072.77 / safe_float(b04.get("T_actual_EMV_sec"), 1.0) * 3.6
    payload = {
        "schema": "compact_v9_u130_init20_postprocess.v1",
        "generated_at": utc_now(),
        "run_id": run_id,
        "tau_scale": args.tau_scale,
        "top20_csv": rel(run_metrics_dir / "top20_ranked.csv"),
        "segment_csv": rel(segment_csv),
        "tau_csv": rel(tau_csv),
        "archive_manifest_json": rel(archive_json),
        "review_md": rel(review_md),
        "b04_validation": b04,
        "b4_validation": b4,
        "b04_ev_speed_kmh": round(b04_ev_speed, 3),
        "top20": top20,
        "candidate_counts": candidate_status_counts(top20),
        "best_candidate_focus_segments": best_candidate_focus_segments(top20, segment_rows),
        "best_candidate_tau_focus": best_candidate_tau_focus(top20, tau_rows),
        "preflight_warnings": PREFLIGHT_WARNINGS,
    }
    write_review_doc(review_md, payload)
    write_json(out_dir / "postprocess_summary.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post-process u130 Init20 B4 search outputs.")
    parser.add_argument("--metrics-root", type=Path, default=DEFAULT_METRICS_ROOT)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--tau-scale", type=float, default=0.85)
    args = parser.parse_args(argv)
    payload = run(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
