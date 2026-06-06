#!/usr/bin/env python3
"""Build target-congestion demand variants and audit B4 main-road Tau coverage."""

from __future__ import annotations

import argparse
import csv
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = Path(__file__).resolve().parent
TDATA_ROOT = PIPELINE_DIR / "tdata_signal"
DEMAND_DIR = PROJECT_ROOT / "data_prepared/compact_v9/demand"
STAGE1_CSV = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1/b4_approach_storage_link_plan.csv"
BASE_DEMAND = DEMAND_DIR / "background_routes_compact_v9_B04_reality_4000_sustained.rou.xml"

AUDIT_CSV = TDATA_ROOT / "b4_mainroad_tau_control_audit.csv"
AUDIT_JSON = TDATA_ROOT / "b4_mainroad_tau_control_audit.json"
SUMMARY_MD = TDATA_ROOT / "TARGET15_DEMAND_AND_TAU_AUDIT_SUMMARY_KO.md"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def read_csv(path: Path) -> list[dict[str, str]]:
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


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clone_vehicle(vehicle: ET.Element, veh_id: str, depart: float) -> ET.Element:
    attrs = dict(vehicle.attrib)
    attrs["id"] = veh_id
    attrs["depart"] = f"{depart:.2f}"
    attrs.setdefault("departLane", "best")
    attrs.setdefault("departPos", "random_free")
    attrs.setdefault("departSpeed", "max")
    return ET.Element("vehicle", attrs)


def bounded_depart(value: float, begin: float = 120.0, end: float = 3900.0) -> float:
    return max(begin, min(end, value))


def jitter(index: int, amplitude: float) -> float:
    raw = ((index * 37 + 17) % 101) / 100.0
    return (raw - 0.5) * 2.0 * amplitude


def frontload_depart(index: int, begin: float, end: float) -> float:
    span = max(1.0, end - begin)
    frac = ((index * 53 + 19) % 1000) / 1000.0
    wave = 0.035 * span * math.sin(index * 1.618)
    return bounded_depart(begin + frac * span + wave, begin, end)


def vehicle_depart(vehicle: ET.Element) -> float:
    return safe_float(vehicle.get("depart"), 0.0)


def is_upbound_pressure_vehicle(vehicle: ET.Element) -> bool:
    route = str(vehicle.get("route", ""))
    return "upbound" in route or "_up_" in vehicle.get("id", "")


def build_demand_variant(
    base_demand: Path,
    output_demand: Path,
    *,
    scale: float,
    warmup_factor: float,
    warmup_begin: float,
    warmup_end: float,
) -> dict[str, Any]:
    tree = ET.parse(base_demand)
    root = tree.getroot()
    original_vehicles = list(root.findall("vehicle"))
    for vehicle in original_vehicles:
        root.remove(vehicle)

    clones: list[ET.Element] = []
    extra_uniform_count = max(0, int(round(len(original_vehicles) * max(0.0, scale - 1.0))))
    for index in range(extra_uniform_count):
        source = original_vehicles[(index * 1103515245 + 12345) % len(original_vehicles)]
        depart = bounded_depart(vehicle_depart(source) + jitter(index, 22.0))
        clones.append(clone_vehicle(source, f"{source.get('id')}_target15_u{index:05d}", depart))

    pressure_sources = [vehicle for vehicle in original_vehicles if is_upbound_pressure_vehicle(vehicle)] or original_vehicles
    warmup_count = max(0, int(round(len(original_vehicles) * max(0.0, warmup_factor))))
    for index in range(warmup_count):
        source = pressure_sources[(index * 2654435761 + 1013904223) % len(pressure_sources)]
        depart = frontload_depart(index, warmup_begin, warmup_end)
        clones.append(clone_vehicle(source, f"{source.get('id')}_target15_w{index:05d}", depart))

    all_vehicles = original_vehicles + clones
    all_vehicles.sort(key=lambda item: (vehicle_depart(item), str(item.get("id", ""))))
    for vehicle in all_vehicles:
        root.append(vehicle)

    output_demand.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_demand, encoding="UTF-8", xml_declaration=True)

    bins = Counter(int(vehicle_depart(vehicle) // 300) * 300 for vehicle in all_vehicles)
    route_prefixes = Counter(str(vehicle.get("route", "")).split("_")[0] for vehicle in all_vehicles)
    summary = {
        "base_demand": rel(base_demand),
        "output_demand": rel(output_demand),
        "scale": scale,
        "warmup_factor": warmup_factor,
        "warmup_begin_sec": warmup_begin,
        "warmup_end_sec": warmup_end,
        "base_vehicle_count": len(original_vehicles),
        "uniform_clone_count": extra_uniform_count,
        "warmup_pressure_clone_count": warmup_count,
        "vehicle_count": len(all_vehicles),
        "vehicle_count_ratio": round(len(all_vehicles) / max(len(original_vehicles), 1), 6),
        "depart_300s_bins": {str(key): bins[key] for key in sorted(bins)},
        "route_prefix_counts": dict(route_prefixes.most_common()),
    }
    write_json(output_demand.with_suffix(".summary.json"), summary)
    return summary


def build_tau_control_audit(stage1_csv: Path, output_csv: Path, output_json: Path) -> dict[str, Any]:
    rows = read_csv(stage1_csv)
    audit_rows: list[dict[str, Any]] = []
    for row in rows:
        controllable = truthy(row.get("controllable"))
        mapped = bool(row.get("mapped_S_segment"))
        has_corridor_edges = bool(row.get("corridor_storage_edges") or row.get("corridor_storage_lanes"))
        has_denominator = safe_float(row.get("corridor_storage_length_m")) > 0.0
        tau_measured = controllable and mapped and has_corridor_edges and has_denominator
        stage3_candidate = controllable and mapped
        audit_rows.append({
            "movement_id": row.get("movement_id", ""),
            "tls_id": row.get("tls_id", ""),
            "mapped_S_segment": row.get("mapped_S_segment", ""),
            "route_order_index": row.get("route_order_index", ""),
            "controllable": controllable,
            "tau_measured_by_runtime": tau_measured,
            "stage3_extension_candidate": stage3_candidate,
            "selected_green_phase": row.get("selected_green_phase", ""),
            "selected_red_phase": row.get("selected_red_phase", ""),
            "red_phase_available": truthy(row.get("red_phase_available")),
            "green_only_no_red_phase": truthy(row.get("green_only_no_red_phase")),
            "local_storage_edges": row.get("local_storage_edges", ""),
            "corridor_storage_edges": row.get("corridor_storage_edges", ""),
            "corridor_storage_length_m": row.get("corridor_storage_length_m", ""),
            "tau_source": "route_order_corridor_edge_queue_sum" if tau_measured else "not_measured",
            "control_scope_note": "B4 Stage3 EV-route main-road movement",
        })

    fields = [
        "movement_id",
        "tls_id",
        "mapped_S_segment",
        "route_order_index",
        "controllable",
        "tau_measured_by_runtime",
        "stage3_extension_candidate",
        "selected_green_phase",
        "selected_red_phase",
        "red_phase_available",
        "green_only_no_red_phase",
        "local_storage_edges",
        "corridor_storage_edges",
        "corridor_storage_length_m",
        "tau_source",
        "control_scope_note",
    ]
    write_csv(output_csv, audit_rows, fields)
    summary = {
        "schema": "b4_mainroad_tau_control_audit.v1",
        "generated_at": utc_now(),
        "stage1_csv": rel(stage1_csv),
        "output_csv": rel(output_csv),
        "mainroad_movement_count": len(audit_rows),
        "controllable_count": sum(1 for row in audit_rows if row["controllable"]),
        "mapped_s_segment_count": sum(1 for row in audit_rows if row["mapped_S_segment"]),
        "tau_measured_count": sum(1 for row in audit_rows if row["tau_measured_by_runtime"]),
        "stage3_extension_candidate_count": sum(1 for row in audit_rows if row["stage3_extension_candidate"]),
        "green_only_no_red_phase_count": sum(1 for row in audit_rows if row["green_only_no_red_phase"]),
        "green_only_no_red_phase_movements": [
            row["movement_id"] for row in audit_rows if row["green_only_no_red_phase"]
        ],
        "scope_note": "This audits B4 Stage3 main-road movements on the EV route, not every tlLogic in the whole SUMO net.",
    }
    write_json(output_json, summary)
    return summary


def write_summary_doc(path: Path, demand_summaries: list[dict[str, Any]], audit_summary: dict[str, Any]) -> None:
    lines = [
        "# Target 15~17 km/h Demand and Tau Audit Summary",
        "",
        f"- generated_at: {utc_now()}",
        f"- Tau audit CSV: {audit_summary.get('output_csv')}",
        f"- main-road B4 movements: {audit_summary.get('mainroad_movement_count')}",
        f"- Tau measured movements: {audit_summary.get('tau_measured_count')}",
        f"- Stage3 extension candidates: {audit_summary.get('stage3_extension_candidate_count')}",
        f"- green-only/no-red movements: {', '.join(audit_summary.get('green_only_no_red_phase_movements', [])) or 'none'}",
        "",
        "## Demand Variants",
        "",
    ]
    for summary in demand_summaries:
        lines.extend([
            f"### {summary['output_demand']}",
            "",
            f"- vehicles: {summary['vehicle_count']} ({summary['vehicle_count_ratio']}x of base)",
            f"- uniform clones: {summary['uniform_clone_count']}",
            f"- warm-up pressure clones: {summary['warmup_pressure_clone_count']}",
            f"- warm-up pressure window: {summary['warmup_begin_sec']}~{summary['warmup_end_sec']} sec",
            f"- 300s bins: {json.dumps(summary['depart_300s_bins'], ensure_ascii=False)}",
            "",
        ])
    lines.extend([
        "## Runtime Meaning",
        "",
        "- B4 Stage3 now computes original Tau as corridor queue proxy / corridor storage length for every controllable movement with mapped_S_segment.",
        "- The control scope is the EV-route main-road Stage1 movement set. Whole-map static TLS timings remain in the net, but B4 does not actively extend every non-route side-street TLS.",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate target-speed demand variants and B4 Tau coverage audit.")
    parser.add_argument("--base-demand", type=Path, default=BASE_DEMAND)
    parser.add_argument("--stage1-csv", type=Path, default=STAGE1_CSV)
    parser.add_argument("--variant", action="append", default=[], help="name:scale:warmup_factor, e.g. s160:1.45:0.15")
    parser.add_argument("--warmup-begin", type=float, default=240.0)
    parser.add_argument("--warmup-end", type=float, default=900.0)
    args = parser.parse_args(argv)

    variant_specs = args.variant or ["s145:1.35:0.10", "s160:1.45:0.15", "s175:1.55:0.20"]
    demand_summaries: list[dict[str, Any]] = []
    for spec in variant_specs:
        parts = spec.split(":")
        if len(parts) != 3:
            raise SystemExit(f"invalid variant spec: {spec}")
        name, scale_text, warmup_text = parts
        output = DEMAND_DIR / f"background_routes_compact_v9_B04_target15_{name}.rou.xml"
        demand_summaries.append(build_demand_variant(
            args.base_demand,
            output,
            scale=float(scale_text),
            warmup_factor=float(warmup_text),
            warmup_begin=args.warmup_begin,
            warmup_end=args.warmup_end,
        ))

    audit_summary = build_tau_control_audit(args.stage1_csv, AUDIT_CSV, AUDIT_JSON)
    write_summary_doc(SUMMARY_MD, demand_summaries, audit_summary)
    print(json.dumps({
        "demand": demand_summaries,
        "audit": audit_summary,
        "summary_md": rel(SUMMARY_MD),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
