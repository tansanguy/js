#!/usr/bin/env python3
"""Audit whether original S-segment tau thresholds react in the final B04/B4 run."""

from __future__ import annotations

import csv
import json
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_ROOT = ROOT / "runs/compact_v9_B4/global_reality4000_mild_theta_high_threshold_smoke_20260605"
CASE_B_CSV = ROOT / "data_prepared/compact_v9/b4_stage1/b4_case_b_candidates.csv"
OUT_DIR = ROOT / "09 Compact Corridor Baseline/tdata_signal"
THRESHOLDS = (0.65, 0.70, 0.75, 0.80, 0.85)
HEADWAY_M = 7.5
WARMUP_CUTOFF_SEC = 600.0


def safe_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return ordered[idx]


def load_case_b_segments() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with CASE_B_CSV.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            rows.append(
                {
                    "segment_id": row["segment_id"],
                    "L_b0_m": float(row["L_b0_m"]),
                    "segment_lanes": row["segment_lanes"].split(),
                    "segment_edges": row["segment_edges"].split(),
                    "segment_fill_B0": float(row.get("segment_fill_B0") or 0.0),
                    "mapping_status": row.get("mapping_status", ""),
                }
            )
    return rows


def audit_signal_events(mode: str, signal_events: Path) -> list[dict[str, object]]:
    rows = list(csv.DictReader(signal_events.open("r", encoding="utf-8", newline="")))
    numeric_columns = ("local_fill_100m", "local_fill_80m", "local_fill_120m", "corridor_fill_250m")
    out: list[dict[str, object]] = []
    for column in numeric_columns:
        values = [safe_float(row.get(column)) for row in rows]
        values = [value for value in values if value is not None]
        if not values:
            continue
        out.append(summary_row(mode, f"event_{column}", "ALL", values, "signal_events.csv"))
    return out


def lane_attrs_by_interval(interval: ET.Element) -> dict[str, dict[str, float]]:
    lane_attrs: dict[str, dict[str, float]] = {}
    for lane in interval.iter("lane"):
        attrs: dict[str, float] = {}
        for key, value in lane.attrib.items():
            parsed = safe_float(value)
            if parsed is not None:
                attrs[key] = parsed
        lane_attrs[lane.attrib["id"]] = attrs
    return lane_attrs


def audit_segment_lane_data(mode: str, lane_data: Path, segments: list[dict[str, object]]) -> list[dict[str, object]]:
    by_segment: dict[str, list[dict[str, float]]] = {str(segment["segment_id"]): [] for segment in segments}
    for _event, interval in ET.iterparse(lane_data, events=("end",)):
        if interval.tag != "interval":
            continue
        begin = safe_float(interval.attrib.get("begin")) or 0.0
        end = safe_float(interval.attrib.get("end")) or 0.0
        if end <= WARMUP_CUTOFF_SEC:
            interval.clear()
            continue
        duration = max(end - begin, 1.0)
        lane_attrs = lane_attrs_by_interval(interval)
        for segment in segments:
            segment_id = str(segment["segment_id"])
            segment_lanes = list(segment["segment_lanes"])
            length_m = float(segment["L_b0_m"])
            sampled_seconds = sum(lane_attrs.get(lane, {}).get("sampledSeconds", 0.0) for lane in segment_lanes)
            waiting_seconds = sum(
                lane_attrs.get(lane, {}).get("waitingTime", lane_attrs.get(lane, {}).get("waiting", 0.0))
                for lane in segment_lanes
            )
            avg_vehicle_count = sampled_seconds / duration
            avg_waiting_vehicle_count = waiting_seconds / duration
            avg_vehicle_fill = min(avg_vehicle_count * HEADWAY_M, length_m) / max(length_m, 0.001)
            waiting_fill = min(avg_waiting_vehicle_count * HEADWAY_M, length_m) / max(length_m, 0.001)
            by_segment[segment_id].append(
                {
                    "avg_vehicle_length_fill": avg_vehicle_fill,
                    "waiting_length_fill": waiting_fill,
                }
            )
        interval.clear()
    out: list[dict[str, object]] = []
    for segment_id, samples in by_segment.items():
        for metric in ("avg_vehicle_length_fill", "waiting_length_fill"):
            out.append(
                summary_row(
                    mode,
                    f"original_tau_{metric}",
                    segment_id,
                    [sample[metric] for sample in samples],
                    "laneData.xml_60s_proxy",
                )
            )
    return out


def summary_row(mode: str, metric: str, segment_id: str, values: list[float], source: str) -> dict[str, object]:
    row: dict[str, object] = {
        "mode": mode,
        "metric": metric,
        "segment_id": segment_id,
        "source": source,
        "sample_count": len(values),
        "mean": round(statistics.mean(values), 6) if values else 0.0,
        "p50": round(percentile(values, 0.50), 6),
        "p95": round(percentile(values, 0.95), 6),
        "max": round(max(values), 6) if values else 0.0,
    }
    for threshold in THRESHOLDS:
        hits = sum(value >= threshold for value in values)
        row[f"hit_count_tau_{threshold:.2f}"] = hits
        row[f"hit_ratio_tau_{threshold:.2f}"] = round(hits / len(values), 6) if values else 0.0
    return row


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    segments = load_case_b_segments()
    rows: list[dict[str, object]] = []
    for mode, rel in {
        "B04": "B04/no_control/repeat_001",
        "B4": "B4/B4_MVP_DEFAULT/repeat_001",
    }.items():
        run_dir = RUN_ROOT / rel
        rows.extend(audit_signal_events(mode, run_dir / "signal_events.csv"))
        rows.extend(audit_segment_lane_data(mode, run_dir / "laneData.xml", segments))

    csv_path = OUT_DIR / "original_tau_reactivity_audit.csv"
    json_path = OUT_DIR / "original_tau_reactivity_audit.json"
    fieldnames = list(rows[0].keys()) if rows else []
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {csv_path}")
    print(f"wrote {json_path}")


if __name__ == "__main__":
    main()
