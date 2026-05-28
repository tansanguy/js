#!/usr/bin/env python3
"""Audit Step 9 TOPIS background demand route coverage and screenline counts."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.net_utils import read_sumo_net  # noqa: E402


ACTIVE_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
BACKGROUND_ROUTES_XML = PROJECT_ROOT / "data_prepared/demand/background_routes_am.rou.xml"
MAPPING_CSV = PROJECT_ROOT / "data_prepared/demand/detector_to_screenline_mapping.csv"
SCREENLINE_COUNTS_CSV = PROJECT_ROOT / "data_prepared/demand/topis_screenline_counts_am.csv"
DEMAND_SUMMARY_JSON = PROJECT_ROOT / "data_prepared/demand/background_demand_summary.json"
SMOKE_SUMMARY_JSON = PROJECT_ROOT / "results/metrics/background_vehicle_spawn_smoke_summary.json"
SPINE_EDGES_CSV = PROJECT_ROOT / "data_prepared/routes/corridor_spine_edges.csv"
ROUTESAMPLER_MISMATCH_XML = PROJECT_ROOT / "data_prepared/demand/topis_route_sampler_mismatch_am.xml"

AUDIT_RUN_DIR = PROJECT_ROOT / "runs/background_demand_audit_am"
AUDIT_ADDITIONAL_XML = AUDIT_RUN_DIR / "edge_data.add.xml"
AUDIT_SUMOCFG = AUDIT_RUN_DIR / "scenario.sumocfg"
AUDIT_TRIPINFO_XML = AUDIT_RUN_DIR / "tripinfo.xml"
AUDIT_SUMMARY_XML = AUDIT_RUN_DIR / "summary.xml"
ACTUAL_EDGEDATA_XML = PROJECT_ROOT / "results/metrics/background_actual_edgedata_am.xml"
ACTUAL_EDGE_COUNTS_CSV = PROJECT_ROOT / "results/metrics/background_actual_edge_counts_am.csv"

ROUTE_EDGE_COUNTS_CSV = PROJECT_ROOT / "results/metrics/background_route_edge_counts_am.csv"
EDGE_COVERAGE_SUMMARY_JSON = PROJECT_ROOT / "results/metrics/background_edge_coverage_summary.json"
ZERO_TRAFFIC_EDGES_CSV = PROJECT_ROOT / "results/metrics/background_zero_traffic_edges_am.csv"
SCREENLINE_AUDIT_CSV = PROJECT_ROOT / "results/metrics/background_screenline_count_audit_am.csv"
SPINE_COVERAGE_CSV = PROJECT_ROOT / "results/metrics/background_spine_edge_coverage_am.csv"
TELEPORT_INTERPRETATION_JSON = PROJECT_ROOT / "results/metrics/background_teleport_interpretation.json"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step09_background_demand_audit.log"
STEP9_DOC = PROJECT_ROOT / "docs/Step9.md"


class AuditError(RuntimeError):
    """Expected audit failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise AuditError(f"JSON root must be object: {rel(path)}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def passenger_edges_from_net() -> dict[str, dict[str, Any]]:
    sumo_net = read_sumo_net(ACTIVE_NET)
    edges: dict[str, dict[str, Any]] = {}
    for edge in sumo_net.getEdges():
        edge_id = edge.getID()
        if edge.isSpecial() or edge_id.startswith(":") or not edge.allows("passenger"):
            continue
        edges[edge_id] = {
            "edge_id": edge_id,
            "length_m": round(float(edge.getLength()), 3),
            "lane_count": edge.getLaneNumber(),
            "speed_mps": round(float(edge.getSpeed()), 3),
            "priority": edge.getPriority(),
        }
    if not edges:
        raise AuditError("No passenger edges found in active net")
    return edges


def planned_edge_counts() -> tuple[int, Counter[str]]:
    if not BACKGROUND_ROUTES_XML.is_file():
        raise AuditError(f"Missing background route file: {rel(BACKGROUND_ROUTES_XML)}")
    vehicle_count = 0
    edge_counts: Counter[str] = Counter()
    for _event, elem in ET.iterparse(BACKGROUND_ROUTES_XML, events=("end",)):
        if elem.tag == "route":
            for edge_id in (elem.get("edges") or "").split():
                edge_counts[edge_id] += 1
        elif elem.tag == "vehicle":
            vehicle_count += 1
        elem.clear()
    return vehicle_count, edge_counts


def parse_actual_edgedata(path: Path) -> dict[str, dict[str, float]]:
    counts: dict[str, dict[str, float]] = {}
    if not path.is_file():
        return counts
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return counts
    for edge in root.findall(".//edge"):
        edge_id = edge.get("id")
        if not edge_id:
            continue
        entered = float(edge.get("entered") or edge.get("departed") or 0.0)
        left = float(edge.get("left") or edge.get("arrived") or 0.0)
        sampled = float(edge.get("sampledSeconds") or 0.0)
        counts[edge_id] = {
            "actual_entered_count": counts.get(edge_id, {}).get("actual_entered_count", 0.0) + entered,
            "actual_left_count": counts.get(edge_id, {}).get("actual_left_count", 0.0) + left,
            "sampled_seconds": counts.get(edge_id, {}).get("sampled_seconds", 0.0) + sampled,
        }
    return counts


def write_actual_edge_data_config() -> None:
    AUDIT_RUN_DIR.mkdir(parents=True, exist_ok=True)
    root = ET.Element("additional")
    ET.SubElement(
        root,
        "edgeData",
        {
            "id": "background_actual_edge_counts_am",
            "file": str(ACTUAL_EDGEDATA_XML),
            "begin": "0",
            "end": "86400",
            "freq": "86400",
            "excludeEmpty": "false",
        },
    )
    ET.ElementTree(root).write(AUDIT_ADDITIONAL_XML, encoding="utf-8", xml_declaration=True)


def write_audit_sumocfg() -> None:
    root = ET.Element("configuration")
    input_elem = ET.SubElement(root, "input")
    ET.SubElement(input_elem, "net-file", {"value": str(ACTIVE_NET)})
    ET.SubElement(input_elem, "route-files", {"value": str(BACKGROUND_ROUTES_XML)})
    ET.SubElement(input_elem, "additional-files", {"value": str(AUDIT_ADDITIONAL_XML)})
    output_elem = ET.SubElement(root, "output")
    ET.SubElement(output_elem, "tripinfo-output", {"value": str(AUDIT_TRIPINFO_XML)})
    ET.SubElement(output_elem, "summary-output", {"value": str(AUDIT_SUMMARY_XML)})
    report_elem = ET.SubElement(root, "report")
    ET.SubElement(report_elem, "no-step-log", {"value": "true"})
    ET.SubElement(report_elem, "duration-log.disable", {"value": "true"})
    AUDIT_RUN_DIR.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(AUDIT_SUMOCFG, encoding="utf-8", xml_declaration=True)


def run_actual_edgedata_smoke(lines: list[str]) -> dict[str, Any]:
    sumo = shutil.which("sumo")
    if not sumo:
        raise AuditError("sumo executable not found")
    write_actual_edge_data_config()
    write_audit_sumocfg()
    stdout_log = AUDIT_RUN_DIR / "sumo_stdout.log"
    stderr_log = AUDIT_RUN_DIR / "sumo_stderr.log"
    completed = subprocess.run(
        [sumo, "-c", str(AUDIT_SUMOCFG)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=900,
    )
    stdout_log.write_text(completed.stdout, encoding="utf-8")
    stderr_log.write_text(completed.stderr, encoding="utf-8")
    lines.append(f"actual_edgedata_sumo_exit_code: {completed.returncode}")
    if completed.stderr.strip():
        lines.append(f"actual_edgedata_sumo_stderr_tail: {completed.stderr.strip()[-4000:]}")
    return {
        "exit_code": completed.returncode,
        "sumocfg": rel(AUDIT_SUMOCFG),
        "actual_edgedata_xml": rel(ACTUAL_EDGEDATA_XML),
        "stdout_log": rel(stdout_log),
        "stderr_log": rel(stderr_log),
    }


def parse_route_sampler_mismatch() -> dict[str, dict[str, float]]:
    data: dict[str, dict[str, float]] = {}
    if not ROUTESAMPLER_MISMATCH_XML.is_file():
        return data
    try:
        root = ET.parse(ROUTESAMPLER_MISMATCH_XML).getroot()
    except ET.ParseError:
        return data
    for edge in root.findall(".//edge"):
        edge_id = edge.get("id")
        if not edge_id:
            continue
        data[edge_id] = {
            "route_sampler_measured_count": float(edge.get("measuredCount") or 0.0),
            "route_sampler_deficit": float(edge.get("deficit") or 0.0),
            "route_sampler_geh": float(edge.get("GEH") or 0.0),
        }
    return data


def screenline_audit_rows(
    planned_counts: Counter[str],
    actual_counts: dict[str, dict[str, float]],
    mismatch: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    rows = []
    for row in read_csv(SCREENLINE_COUNTS_CSV):
        edge_id = row["screenline_edge_id"]
        target = float(row["count_600s_edge"])
        planned = float(planned_counts.get(edge_id, 0))
        actual = actual_counts.get(edge_id, {})
        actual_entered = actual.get("actual_entered_count", "")
        planned_error = planned - target
        actual_error = (float(actual_entered) - target) if actual_entered != "" else ""
        rows.append(
            {
                "detector_id": row["detector_id"],
                "road_axis_id": row["road_axis_id"],
                "screenline_edge_id": edge_id,
                "target_count": round(target, 6),
                "planned_count": int(planned),
                "actual_entered_count": round(float(actual_entered), 6) if actual_entered != "" else "",
                "planned_error_abs": round(abs(planned_error), 6),
                "planned_error_pct": round((planned_error / target) * 100.0, 6) if target else "",
                "actual_error_abs": round(abs(actual_error), 6) if actual_error != "" else "",
                "actual_error_pct": round((actual_error / target) * 100.0, 6) if actual_error != "" and target else "",
                "route_sampler_measured_count": mismatch.get(edge_id, {}).get("route_sampler_measured_count", ""),
                "route_sampler_deficit": mismatch.get(edge_id, {}).get("route_sampler_deficit", ""),
                "route_sampler_geh": mismatch.get(edge_id, {}).get("route_sampler_geh", ""),
            }
        )
    return rows


def write_route_edge_counts(
    passenger_edges: dict[str, dict[str, Any]],
    planned_counts: Counter[str],
    actual_counts: dict[str, dict[str, float]],
) -> None:
    rows = []
    for edge_id, meta in sorted(passenger_edges.items()):
        actual = actual_counts.get(edge_id, {})
        rows.append(
            {
                **meta,
                "planned_count": planned_counts.get(edge_id, 0),
                "actual_entered_count": round(actual.get("actual_entered_count", 0.0), 6),
                "actual_left_count": round(actual.get("actual_left_count", 0.0), 6),
                "sampled_seconds": round(actual.get("sampled_seconds", 0.0), 6),
            }
        )
    write_csv(
        ROUTE_EDGE_COUNTS_CSV,
        rows,
        ["edge_id", "length_m", "lane_count", "speed_mps", "priority", "planned_count", "actual_entered_count", "actual_left_count", "sampled_seconds"],
    )


def spine_rows(planned_counts: Counter[str], actual_counts: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    rows = []
    if not SPINE_EDGES_CSV.is_file():
        return rows
    for row in read_csv(SPINE_EDGES_CSV):
        if row.get("is_spine_edge") != "True":
            continue
        edge_id = row["edge_id"]
        actual = actual_counts.get(edge_id, {})
        rows.append(
            {
                "edge_id": edge_id,
                "length_m": row.get("length_m", ""),
                "lane_count": row.get("lane_count", ""),
                "speed_mps": row.get("speed_mps", ""),
                "priority": row.get("priority", ""),
                "spine_score": row.get("spine_score", ""),
                "planned_count": planned_counts.get(edge_id, 0),
                "actual_entered_count": round(actual.get("actual_entered_count", 0.0), 6),
                "actual_left_count": round(actual.get("actual_left_count", 0.0), 6),
            }
        )
    return rows


def teleport_interpretation(smoke_summary: dict[str, Any]) -> dict[str, Any]:
    departed = int(smoke_summary.get("departed_count", 0))
    teleports = int(smoke_summary.get("teleport_count", 0))
    route_errors = int(smoke_summary.get("route_error_count", 0))
    ratio = teleports / departed if departed else 0.0
    if ratio == 0:
        severity = "PASS"
    elif ratio <= 0.05:
        severity = "MILD_WARNING"
    elif ratio <= 0.20:
        severity = "STRONG_WARNING"
    else:
        severity = "SCALE_DOWN_RECOMMENDED"
    return {
        "departed_count": departed,
        "arrived_count": int(smoke_summary.get("arrived_count", 0)),
        "teleport_count": teleports,
        "teleport_ratio": round(ratio, 6),
        "route_error_count": route_errors,
        "interpretation": "routes_valid_but_network_capacity_or_lane_choice_unstable" if teleports else "no_teleport_observed",
        "severity": severity,
        "demand_generation_status": "WARNING" if teleports or route_errors else "PASS",
        "scale_down_recommendation": "try_0.5x_and_0.3x_before_B0_B1_B2_comparison" if ratio > 0.20 else "",
    }


def append_doc_audit_section(summary: dict[str, Any]) -> None:
    marker_title = "## Background Demand Audit"
    current = STEP9_DOC.read_text(encoding="utf-8") if STEP9_DOC.is_file() else "# Step 9 TOPIS AM Background Demand\n"
    marker_index = current.find(marker_title)
    base = (current[:marker_index] if marker_index >= 0 else current).rstrip()
    text = f"""{base}

{marker_title}

Step 9 audit는 기존 `background_routes_am.rou.xml`을 재생성하지 않고, route XML과 audit smoke edgeData output을 기준으로 planned/actual edge usage를 분리해 검증한다.

- audit status: `{summary['final_status']}`
- route XML vehicle count: `{summary['vehicle_count_xml']}`
- smoke departed/arrived: `{summary['smoke_departed_count']}` / `{summary['smoke_arrived_count']}`
- active passenger edge count: `{summary['passenger_edge_count']}`
- planned used edge count: `{summary['planned_used_edge_count']}`
- planned coverage ratio: `{summary['planned_coverage_ratio']}`
- screenline count rows: `{summary['screenline_count_rows']}`
- screenline planned low-achievement count: `{summary['screenline_low_achievement_count']}`
- screenline actual low-achievement count: `{summary['screenline_actual_low_achievement_count']}`
- spine edge count: `{summary['spine_edge_count_total']}`
- spine planned coverage ratio: `{summary['spine_planned_coverage_ratio']}`
- teleport ratio: `{summary['teleport']['teleport_ratio']}`
- teleport severity: `{summary['teleport']['severity']}`

Audit outputs:

- `results/metrics/background_route_edge_counts_am.csv`
- `results/metrics/background_edge_coverage_summary.json`
- `results/metrics/background_zero_traffic_edges_am.csv`
- `results/metrics/background_screenline_count_audit_am.csv`
- `results/metrics/background_spine_edge_coverage_am.csv`
- `results/metrics/background_teleport_interpretation.json`
- `results/metrics/background_actual_edgedata_am.xml`
- `results/metrics/background_actual_edge_counts_am.csv`
- `outputs/logs/step09_background_demand_audit.log`

해석:

- 모든 reduced-map edge에 차량이 있어야 하는 것은 아니다. 전체 edge coverage는 참고 지표다.
- 핵심은 TOPIS screenline target 달성, spine/corridor coverage, detector road-axis flow 존재 여부다.
- 현재 teleport가 많으면 route 생성 실패가 아니라 capacity/lane-choice instability로 해석하고, B0/B1/B2 비교 전 0.5x 또는 0.3x scale-down audit 후보를 검토한다.
"""
    STEP9_DOC.write_text(text + "\n", encoding="utf-8")


def main() -> int:
    generated_at = utc_now()
    lines = ["Step 9 background demand audit", "==============================", f"generated_at: {generated_at}"]
    required = [ACTIVE_NET, BACKGROUND_ROUTES_XML, MAPPING_CSV, SCREENLINE_COUNTS_CSV, DEMAND_SUMMARY_JSON, SMOKE_SUMMARY_JSON, SPINE_EDGES_CSV]
    try:
        missing = [rel(path) for path in required if not path.is_file()]
        if missing:
            raise AuditError(f"Missing required inputs: {missing}")
        demand_summary = read_json(DEMAND_SUMMARY_JSON)
        smoke_summary = read_json(SMOKE_SUMMARY_JSON)
        passenger_edges = passenger_edges_from_net()
        vehicle_count, planned_counts = planned_edge_counts()
        actual_smoke = run_actual_edgedata_smoke(lines)
        actual_counts = parse_actual_edgedata(ACTUAL_EDGEDATA_XML)
        mismatch = parse_route_sampler_mismatch()

        write_route_edge_counts(passenger_edges, planned_counts, actual_counts)
        actual_rows = []
        for edge_id, counts in sorted(actual_counts.items()):
            actual_rows.append(
                {
                    "edge_id": edge_id,
                    "actual_entered_count": round(counts.get("actual_entered_count", 0.0), 6),
                    "actual_left_count": round(counts.get("actual_left_count", 0.0), 6),
                    "sampled_seconds": round(counts.get("sampled_seconds", 0.0), 6),
                }
            )
        write_csv(ACTUAL_EDGE_COUNTS_CSV, actual_rows, ["edge_id", "actual_entered_count", "actual_left_count", "sampled_seconds"])

        screenline_rows = screenline_audit_rows(planned_counts, actual_counts, mismatch)
        write_csv(
            SCREENLINE_AUDIT_CSV,
            screenline_rows,
            [
                "detector_id",
                "road_axis_id",
                "screenline_edge_id",
                "target_count",
                "planned_count",
                "actual_entered_count",
                "planned_error_abs",
                "planned_error_pct",
                "actual_error_abs",
                "actual_error_pct",
                "route_sampler_measured_count",
                "route_sampler_deficit",
                "route_sampler_geh",
            ],
        )

        spine = spine_rows(planned_counts, actual_counts)
        write_csv(
            SPINE_COVERAGE_CSV,
            spine,
            ["edge_id", "length_m", "lane_count", "speed_mps", "priority", "spine_score", "planned_count", "actual_entered_count", "actual_left_count"],
        )

        zero_rows = []
        for edge_id, meta in sorted(passenger_edges.items()):
            planned = planned_counts.get(edge_id, 0)
            actual = actual_counts.get(edge_id, {}).get("actual_entered_count", 0.0)
            if planned == 0 or actual == 0:
                zero_rows.append(
                    {
                        **meta,
                        "planned_count": planned,
                        "actual_entered_count": round(actual, 6),
                        "zero_planned": planned == 0,
                        "zero_actual": actual == 0,
                    }
                )
        write_csv(
            ZERO_TRAFFIC_EDGES_CSV,
            zero_rows,
            ["edge_id", "length_m", "lane_count", "speed_mps", "priority", "planned_count", "actual_entered_count", "zero_planned", "zero_actual"],
        )

        teleport = teleport_interpretation(smoke_summary)
        write_json(TELEPORT_INTERPRETATION_JSON, teleport)

        planned_used = {edge_id for edge_id, count in planned_counts.items() if count > 0 and edge_id in passenger_edges}
        actual_used = {edge_id for edge_id, count in actual_counts.items() if count.get("actual_entered_count", 0.0) > 0 and edge_id in passenger_edges}
        spine_used = [row for row in spine if int(row["planned_count"]) > 0]
        spine_actual_used = [row for row in spine if float(row["actual_entered_count"]) > 0]
        screenline_low = [
            row for row in screenline_rows
            if row["target_count"] and abs(float(row["planned_error_pct"])) > 10.0
        ]
        screenline_actual_low = [
            row for row in screenline_rows
            if row["target_count"] and row["actual_error_pct"] != "" and abs(float(row["actual_error_pct"])) > 10.0
        ]
        status = "PASS"
        warnings = []
        failures = []
        if vehicle_count != int(demand_summary.get("route_sampler_vehicle_count", -1)):
            failures.append("vehicle_count_mismatch_demand_summary")
        if vehicle_count != int(demand_summary.get("smoke", {}).get("expected_vehicle_count", -1)):
            failures.append("vehicle_count_mismatch_smoke_expected")
        if vehicle_count != int(smoke_summary.get("departed_count", -1)):
            failures.append("vehicle_count_mismatch_departed")
        if actual_smoke["exit_code"] != 0:
            failures.append("actual_edgedata_smoke_failed")
        if teleport["teleport_count"] > 0:
            warnings.append("teleports_present")
        if screenline_low:
            warnings.append("screenline_planned_error_gt_10pct")
        if screenline_actual_low:
            warnings.append("screenline_actual_error_gt_10pct")
        if failures:
            status = "FAIL"
        elif warnings:
            status = "WARNING"

        coverage_summary = {
            "generated_at": generated_at,
            "final_status": status,
            "active_net": rel(ACTIVE_NET),
            "background_routes": rel(BACKGROUND_ROUTES_XML),
            "vehicle_count_xml": vehicle_count,
            "route_sampler_vehicle_count": demand_summary.get("route_sampler_vehicle_count"),
            "smoke_expected_vehicle_count": demand_summary.get("smoke", {}).get("expected_vehicle_count"),
            "smoke_departed_count": smoke_summary.get("departed_count"),
            "smoke_arrived_count": smoke_summary.get("arrived_count"),
            "passenger_edge_count": len(passenger_edges),
            "planned_used_edge_count": len(planned_used),
            "actual_used_edge_count": len(actual_used),
            "planned_zero_edge_count": len(passenger_edges) - len(planned_used),
            "actual_zero_edge_count": len(passenger_edges) - len(actual_used),
            "planned_coverage_ratio": round(len(planned_used) / len(passenger_edges), 6),
            "actual_coverage_ratio": round(len(actual_used) / len(passenger_edges), 6),
            "screenline_count_rows": len(screenline_rows),
            "screenline_low_achievement_count": len(screenline_low),
            "screenline_actual_low_achievement_count": len(screenline_actual_low),
            "spine_edge_count_total": len(spine),
            "spine_edge_used_count": len(spine_used),
            "spine_actual_used_count": len(spine_actual_used),
            "spine_planned_coverage_ratio": round(len(spine_used) / len(spine), 6) if spine else 0.0,
            "spine_actual_coverage_ratio": round(len(spine_actual_used) / len(spine), 6) if spine else 0.0,
            "teleport": teleport,
            "warnings": warnings,
            "failures": failures,
            "actual_edgedata_smoke": actual_smoke,
            "outputs": [
                rel(ROUTE_EDGE_COUNTS_CSV),
                rel(EDGE_COVERAGE_SUMMARY_JSON),
                rel(ZERO_TRAFFIC_EDGES_CSV),
                rel(SCREENLINE_AUDIT_CSV),
                rel(SPINE_COVERAGE_CSV),
                rel(TELEPORT_INTERPRETATION_JSON),
                rel(ACTUAL_EDGEDATA_XML),
                rel(ACTUAL_EDGE_COUNTS_CSV),
                rel(LOG_PATH),
            ],
        }
        write_json(EDGE_COVERAGE_SUMMARY_JSON, coverage_summary)
        append_doc_audit_section(coverage_summary)
        lines.extend(
            [
                f"final_status: {status}",
                f"vehicle_count_xml: {vehicle_count}",
                f"planned_used_edge_count: {len(planned_used)}",
                f"actual_used_edge_count: {len(actual_used)}",
                f"screenline_low_achievement_count: {len(screenline_low)}",
                f"screenline_actual_low_achievement_count: {len(screenline_actual_low)}",
                f"spine_planned_coverage_ratio: {coverage_summary['spine_planned_coverage_ratio']}",
                f"teleport_ratio: {teleport['teleport_ratio']}",
                f"teleport_severity: {teleport['severity']}",
            ]
        )
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if status in {"PASS", "WARNING"} else 1
    except (AuditError, OSError, ET.ParseError, subprocess.TimeoutExpired, ValueError, RuntimeError, ImportError) as exc:
        lines.extend(["final_status: FAIL", f"failure_reason: {exc}"])
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
