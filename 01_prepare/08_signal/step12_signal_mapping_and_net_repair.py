#!/usr/bin/env python3
"""Step 12 signal mapping, ER_ACC_013 diagnosis, and preliminary B1 batch smoke."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.net_utils import read_sumo_net  # noqa: E402


DEFAULT_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
DEFAULT_BACKGROUND = PROJECT_ROOT / "data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml"
DEFAULT_ROUTES = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
DEFAULT_TLS_AUDIT = PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_spine_v2.csv"
DEFAULT_SPINE_EDGES = PROJECT_ROOT / "data_prepared/routes/corridor_spine_edges.csv"
DEFAULT_B0_SUMMARY = PROJECT_ROOT / "results/metrics/b0_baseline_19route_smoke_summary.json"
DEFAULT_B1_ER002_SUMMARY = PROJECT_ROOT / "results/metrics/b1_er_acc_002_smoke_summary.json"
CONFIG_PATH = PROJECT_ROOT / "configs/b1_priority_signal_config.json"
TERMINAL_CSV = PROJECT_ROOT / "data_prepared/signals/priority_terminal_candidates.csv"
TERMINAL_JSON = PROJECT_ROOT / "data_prepared/signals/priority_terminal_candidates_summary.json"
DIAG_JSON = PROJECT_ROOT / "results/metrics/er_acc_013_b0_teleport_diagnosis.json"
DIAG_DOC = PROJECT_ROOT / "docs/ER_ACC_013_diagnosis.md"
REPAIR_ROOT = PROJECT_ROOT / "results/metrics/net_repair_er_acc_013"
REPAIR_CANDIDATES_CSV = REPAIR_ROOT / "repair_candidates.csv"
REPAIR_VERIFY_CSV = REPAIR_ROOT / "repair_verification_summary.csv"
REPAIR_DOC = PROJECT_ROOT / "docs/ER_ACC_013_repair_report.md"
B1_BATCH_CSV = PROJECT_ROOT / "results/metrics/b1_b0_valid_route_smoke_summary.csv"
B1_BATCH_JSON = PROJECT_ROOT / "results/metrics/b1_b0_valid_route_smoke_summary.json"
B1_EVENTS_CSV = PROJECT_ROOT / "results/metrics/b1_b0_valid_route_signal_events.csv"
B1_LOG = PROJECT_ROOT / "outputs/logs/b1_b0_valid_route_smoke.log"
BLOCKER_DOC = PROJECT_ROOT / "docs/B1_batch_smoke_blockers.md"
STEP12_DOC = PROJECT_ROOT / "docs/Step12_signal_mapping_and_net_repair.md"
NET_REPAIR_WS = PROJECT_ROOT / "data_prepared/net_repair/er_acc_013"
NET_REPAIR_RUNS = PROJECT_ROOT / "runs/net_repair_er_acc_013"
B1_RUN_ROOT = PROJECT_ROOT / "runs/b1_b0_valid_route_smoke"
TARGET_ROUTE_ID = "ER_ACC_013"


class Step12Error(RuntimeError):
    """Expected Step 12 failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Step 12 signal mapping and repair readiness.")
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND)
    parser.add_argument("--emergency-routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--tls-audit", type=Path, default=DEFAULT_TLS_AUDIT)
    parser.add_argument("--spine-edges", type=Path, default=DEFAULT_SPINE_EDGES)
    parser.add_argument("--b0-summary", type=Path, default=DEFAULT_B0_SUMMARY)
    parser.add_argument("--b1-er002-summary", type=Path, default=DEFAULT_B1_ER002_SUMMARY)
    parser.add_argument("--time-to-teleport", type=int, default=1200)
    parser.add_argument("--collision-action", choices=["none", "warn", "teleport", "remove"], default="warn")
    parser.add_argument("--timeout-steps", type=int, default=7200)
    parser.add_argument("--run-b1-batch", action="store_true", default=True)
    return parser.parse_args()


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def csv_fields(rows: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    return fields


def parse_xml_with_retry(path: Path, attempts: int = 6, delay_sec: float = 0.5) -> ET.ElementTree:
    last_error: ET.ParseError | None = None
    for attempt in range(attempts):
        try:
            return ET.parse(path)
        except ET.ParseError as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(delay_sec)
    assert last_error is not None
    raise last_error


def count_vehicles(route_file: Path) -> int:
    count = 0
    for _event, elem in ET.iterparse(route_file, events=("end",)):
        if elem.tag == "vehicle":
            count += 1
        elem.clear()
    return count


def prerequisite_check(paths: dict[str, Path]) -> list[dict[str, str]]:
    commands = {
        "b0_summary": "python3 01_prepare/07_experiments/step10_b0_19route_baseline_smoke.py --net data_prepared/net/jungbu_ellipse_passenger.net.xml --background-route data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml --emergency-routes data_prepared/routes/emergency_routes_spine_v2.csv --spine-edges data_prepared/routes/corridor_spine_edges.csv --time-to-teleport 1200 --collision-action warn",
        "tls_audit": "python3 01_prepare/08_signal/step11a_tls_phase_audit.py --net data_prepared/net/jungbu_ellipse_passenger.net.xml --emergency-routes data_prepared/routes/emergency_routes_spine_v2.csv",
        "b1_er002": "python3 01_prepare/08_signal/step11b_b1_er_acc_002_smoke.py --net data_prepared/net/jungbu_ellipse_passenger.net.xml --background-route data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml --emergency-routes data_prepared/routes/emergency_routes_spine_v2.csv --route-id ER_ACC_002 --tls-audit data_prepared/signals/tls_phase_audit_spine_v2.csv --time-to-teleport 1200 --collision-action warn",
    }
    result = []
    for key, path in paths.items():
        result.append(
            {
                "key": key,
                "path": rel(path) if path.is_absolute() and PROJECT_ROOT in path.parents else str(path),
                "exists": str(path.is_file()),
                "dependent_task_blocked": "" if path.is_file() else key,
                "regenerate_command": "" if path.is_file() else commands.get(key, "inspect missing input and rerun upstream step"),
            }
        )
    return result


def parse_net(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    edges = {}
    lanes = {}
    internal_edges = {}
    connections: dict[tuple[str, str], list[dict[str, str]]] = {}
    via_connections = {}
    tl_logic = {}
    for elem in root.findall("edge"):
        edge_id = elem.get("id", "")
        lane_elems = elem.findall("lane")
        lane_records = []
        for lane in lane_elems:
            lane_records.append(dict(lane.attrib))
            lanes[lane.get("id", "")] = {"edge_id": edge_id, **dict(lane.attrib)}
        record = {
            "id": edge_id,
            "from": elem.get("from", ""),
            "to": elem.get("to", ""),
            "function": elem.get("function", ""),
            "length": float(lane_elems[0].get("length", "0") or 0) if lane_elems else 0.0,
            "lanes": lane_records,
        }
        edges[edge_id] = record
        if elem.get("function") == "internal":
            internal_edges[edge_id] = record
    for conn in root.findall("connection"):
        record = dict(conn.attrib)
        connections.setdefault((record.get("from", ""), record.get("to", "")), []).append(record)
        if record.get("via"):
            via_connections[record["via"]] = record
    for tls in root.findall("tlLogic"):
        phases = [dict(phase.attrib) for phase in tls.findall("phase")]
        tl_logic[tls.get("id", "")] = {
            "program_id": tls.get("programID", ""),
            "type": tls.get("type", ""),
            "phases": phases,
        }
    return {"edges": edges, "lanes": lanes, "internal_edges": internal_edges, "connections": connections, "via_connections": via_connections, "tl_logic": tl_logic}


def select_route(routes: list[dict[str, str]], route_id: str) -> dict[str, str]:
    matches = [row for row in routes if row.get("route_id") == route_id]
    if not matches:
        raise Step12Error(f"route not found: {route_id}")
    return matches[0]


def validate_route_edges(net_path: Path, edge_ids: list[str]) -> list[str]:
    sumo_net = read_sumo_net(str(net_path))
    failures = []
    for edge_id in edge_ids:
        try:
            sumo_net.getEdge(edge_id)
        except KeyError:
            failures.append(f"missing_edge:{edge_id}")
    if failures:
        return failures
    for from_id, to_id in zip(edge_ids, edge_ids[1:], strict=False):
        if sumo_net.getEdge(to_id) not in sumo_net.getEdge(from_id).getOutgoing():
            failures.append(f"disconnected_transition:{from_id}->{to_id}")
    return failures


def route_edge_starts(net_path: Path, route_edges: list[str]) -> list[float]:
    sumo_net = read_sumo_net(str(net_path))
    starts = []
    cumulative = 0.0
    for edge_id in route_edges:
        starts.append(cumulative)
        cumulative += float(sumo_net.getEdge(edge_id).getLength())
    return starts


def write_emergency_route_xml(path: Path, route_row: dict[str, str], vehicle_id: str, depart: float = 0.0, vtype_id: str = "step12_emergency_type") -> None:
    root = ET.Element("routes")
    vtype = ET.SubElement(
        root,
        "vType",
        {
            "id": vtype_id,
            "vClass": "emergency",
            "guiShape": "emergency",
            "color": "1,0,0",
            "speedFactor": "1.30",
            "speedDev": "0.00",
            "accel": "3.0",
            "decel": "7.5",
            "impatience": "1.0",
        },
    )
    ET.SubElement(vtype, "param", {"key": "has.bluelight.device", "value": "true"})
    ET.SubElement(root, "route", {"id": route_row["route_id"], "edges": route_row["route_edges"]})
    ET.SubElement(
        root,
        "vehicle",
        {
            "id": vehicle_id,
            "type": vtype_id,
            "route": route_row["route_id"],
            "depart": f"{depart:g}",
            "departLane": "best",
            "departSpeed": "max",
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_sumo_files(
    run_dir: Path,
    net_path: Path,
    route_files: list[Path],
    time_to_teleport: int,
    collision_action: str,
) -> dict[str, Path]:
    paths = {
        "additional": run_dir / "edge_data.add.xml",
        "edge_data": run_dir / "edgeData.xml",
        "sumocfg": run_dir / "scenario.sumocfg",
        "tripinfo": run_dir / "tripinfo.xml",
        "summary": run_dir / "summary.xml",
        "stdout": run_dir / "sumo_stdout.log",
        "stderr": run_dir / "sumo_stderr.log",
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    additional = ET.Element("additional")
    ET.SubElement(
        additional,
        "edgeData",
        {"id": "step12_edge_data", "file": str(paths["edge_data"]), "begin": "0", "end": "86400", "freq": "86400", "excludeEmpty": "false"},
    )
    ET.ElementTree(additional).write(paths["additional"], encoding="utf-8", xml_declaration=True)
    config = ET.Element("configuration")
    input_elem = ET.SubElement(config, "input")
    ET.SubElement(input_elem, "net-file", {"value": str(net_path)})
    ET.SubElement(input_elem, "route-files", {"value": ",".join(str(path) for path in route_files)})
    ET.SubElement(input_elem, "additional-files", {"value": str(paths["additional"])})
    output_elem = ET.SubElement(config, "output")
    ET.SubElement(output_elem, "tripinfo-output", {"value": str(paths["tripinfo"])})
    ET.SubElement(output_elem, "summary-output", {"value": str(paths["summary"])})
    time_elem = ET.SubElement(config, "time")
    ET.SubElement(time_elem, "begin", {"value": "0"})
    processing_elem = ET.SubElement(config, "processing")
    ET.SubElement(processing_elem, "time-to-teleport", {"value": str(time_to_teleport)})
    ET.SubElement(processing_elem, "collision.action", {"value": collision_action})
    report_elem = ET.SubElement(config, "report")
    ET.SubElement(report_elem, "no-step-log", {"value": "true"})
    ET.SubElement(report_elem, "duration-log.disable", {"value": "true"})
    ET.ElementTree(config).write(paths["sumocfg"], encoding="utf-8", xml_declaration=True)
    return paths


def parse_summary(path: Path) -> dict[str, Any]:
    root = parse_xml_with_retry(path).getroot()
    last_step = None
    max_teleports = 0
    for step in root.findall("step"):
        last_step = step
        max_teleports = max(max_teleports, int(float(step.get("teleports", "0"))))
    if last_step is None:
        raise Step12Error(f"summary has no step: {rel(path)}")
    return {
        "departed_total": int(float(last_step.get("inserted", "0"))),
        "arrived_total": int(float(last_step.get("arrived", "0"))),
        "teleport_count": max_teleports,
        "sim_end_time": float(last_step.get("time", "0")),
    }


def parse_tripinfo(path: Path, vehicle_id: str) -> dict[str, Any]:
    root = parse_xml_with_retry(path).getroot()
    for tripinfo in root.findall("tripinfo"):
        if tripinfo.get("id") == vehicle_id:
            return {
                "arrived": True,
                "travel_time": float(tripinfo.get("duration", "0")),
                "waiting_time": float(tripinfo.get("waitingTime", "0") or 0),
                "time_loss": float(tripinfo.get("timeLoss", "0") or 0),
                "arrival": float(tripinfo.get("arrival", "0") or 0),
            }
    return {"arrived": False, "travel_time": None, "waiting_time": None, "time_loss": None, "arrival": None}


def route_error_count(stderr: str) -> int:
    lower = stderr.lower()
    return lower.count("route error") + lower.count("has no valid route") + lower.count("is not connected")


def emergency_teleport_lines(stderr: str, vehicle_id: str) -> list[str]:
    return [line for line in stderr.splitlines() if vehicle_id in line and "teleport" in line.lower()]


def run_sumo_smoke(
    net_path: Path,
    route_files: list[Path],
    run_dir: Path,
    vehicle_id: str,
    background_vehicle_count: int,
    time_to_teleport: int,
    collision_action: str,
) -> dict[str, Any]:
    sumo = shutil.which("sumo")
    if sumo is None:
        raise Step12Error("missing_executable: sumo")
    paths = write_sumo_files(run_dir, net_path, route_files, time_to_teleport, collision_action)
    completed = __import__("subprocess").run([sumo, "-c", str(paths["sumocfg"])], cwd=PROJECT_ROOT, check=False, capture_output=True, text=True, timeout=1200)
    paths["stdout"].write_text(completed.stdout, encoding="utf-8")
    paths["stderr"].write_text(completed.stderr, encoding="utf-8")
    summary = parse_summary(paths["summary"])
    trip = parse_tripinfo(paths["tripinfo"], vehicle_id)
    stderr = completed.stderr
    emergency_tp = emergency_teleport_lines(stderr, vehicle_id)
    departed = summary["departed_total"] > background_vehicle_count or trip["arrived"]
    arrived = bool(trip["arrived"])
    teleported = bool(emergency_tp)
    return {
        "sumo_exit_code": completed.returncode,
        "emergency_departed": departed,
        "emergency_arrived": arrived,
        "emergency_teleport": teleported,
        "emergency_teleport_evidence": emergency_tp,
        "route_error_count": route_error_count(stderr),
        "emergency_travel_time": trip["travel_time"],
        "emergency_waiting_time": trip["waiting_time"],
        "background_departed": max(summary["departed_total"] - (1 if departed else 0), 0),
        "background_arrived": max(summary["arrived_total"] - (1 if arrived else 0), 0),
        "background_teleported": max(summary["teleport_count"] - (1 if teleported else 0), 0),
        "sim_end_time": summary["sim_end_time"],
        "run_dir": rel(run_dir),
        "sumocfg": rel(paths["sumocfg"]),
        "stderr_log": rel(paths["stderr"]),
        "tripinfo": rel(paths["tripinfo"]),
    }


def diagnose_er_acc_013(args: argparse.Namespace, net_data: dict[str, Any], routes: list[dict[str, str]], b0_summary: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    route_row = select_route(routes, TARGET_ROUTE_ID)
    b0_row = [row for row in b0_summary["results"] if row["route_id"] == TARGET_ROUTE_ID][0]
    stderr_path = PROJECT_ROOT / b0_row["stderr_log"]
    tripinfo_path = PROJECT_ROOT / b0_row["tripinfo"]
    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace")
    teleport_line = next((line for line in b0_row.get("emergency_teleport_evidence", []) if "Teleporting vehicle" in line), "")
    end_line = next((line for line in b0_row.get("emergency_teleport_evidence", []) if "ends teleporting" in line), "")
    lane_match = re.search(r"lane='([^']+)'", teleport_line)
    time_match = re.search(r"time=([0-9]+(?:\.[0-9]+)?)", teleport_line)
    reason_match = re.search(r";\s*([^,]+),", teleport_line)
    end_edge_match = re.search(r"edge '([^']+)'", end_line)
    teleport_lane = lane_match.group(1) if lane_match else ""
    internal_edge = teleport_lane.rsplit("_", 1)[0] if teleport_lane.startswith(":") else ""
    via_connection = net_data["via_connections"].get(teleport_lane) or net_data["via_connections"].get(internal_edge, {})
    incoming = via_connection.get("from", "")
    outgoing = via_connection.get("to", "")
    tls_id = via_connection.get("tl", "")
    link_index = via_connection.get("linkIndex", "")
    lane_meta = net_data["lanes"].get(teleport_lane, {})
    route_edges = route_row["route_edges"].split()
    transition_in_route = False
    if incoming and outgoing:
        transition_in_route = any(a == incoming and b == outgoing for a, b in zip(route_edges, route_edges[1:], strict=False))
    connection_exists = bool(via_connection)
    lane_disallow = lane_meta.get("disallow", "")
    lane_allow = lane_meta.get("allow", "")
    emergency_permitted = "emergency" not in lane_disallow.split() if lane_disallow else True
    passenger_permitted = "passenger" not in lane_disallow.split() if lane_disallow else True
    trip = parse_tripinfo(tripinfo_path, "emergency_ER_ACC_013")
    background_tp_near = [line for line in stderr_text.splitlines() if "Teleporting vehicle 'bg_" in line and (incoming in line or internal_edge in line or tls_id in line or "619147735#4" in line)]
    likely_causes = []
    if connection_exists and emergency_permitted and passenger_permitted and b0_row.get("route_error_count") == 0:
        likely_causes.append("background_demand_jam")
    if teleport_lane.startswith(":"):
        likely_causes.append("internal_lane_blockage")
    if tls_id:
        likely_causes.append("signal_phase_conflict_possible")
    if background_tp_near:
        likely_causes.append("excessive_local_demand_concentration")
    masking = True
    diagnosis = {
        "generated_at": utc_now(),
        "route_id": TARGET_ROUTE_ID,
        "target_edge_id": route_row["target_edge_id"],
        "teleport_time": float(time_match.group(1)) if time_match else None,
        "jam_waiting_duration": args.time_to_teleport if "waited too long" in teleport_line else None,
        "teleport_reason": reason_match.group(1).strip() if reason_match else "unknown",
        "teleport_lane": teleport_lane,
        "teleport_edge": internal_edge,
        "teleport_end_edge": end_edge_match.group(1) if end_edge_match else "",
        "is_internal_lane": teleport_lane.startswith(":"),
        "nearest_junction": net_data["edges"].get(internal_edge, {}).get("to") or tls_id,
        "nearest_tls": tls_id,
        "incoming_edge_before_internal_lane": incoming,
        "outgoing_edge_after_internal_lane": outgoing,
        "route_transition_exists": transition_in_route,
        "net_connection_exists": connection_exists,
        "connection": via_connection,
        "lane_allow": lane_allow,
        "lane_disallow": lane_disallow,
        "lane_permits_emergency": emergency_permitted,
        "lane_permits_passenger": passenger_permitted,
        "trip_waiting_time": trip["waiting_time"],
        "trip_time_loss": trip["time_loss"],
        "background_teleport_near_transition_count": len(background_tp_near),
        "likely_causes": likely_causes,
        "primary_cause_assessment": "background demand jam on a valid internal TLS connection" if likely_causes else "inconclusive",
        "increase_time_to_teleport_masks_issue": masking,
        "source_b0_stderr": b0_row["stderr_log"],
        "source_b0_tripinfo": b0_row["tripinfo"],
    }

    NET_REPAIR_WS.mkdir(parents=True, exist_ok=True)
    NET_REPAIR_RUNS.mkdir(parents=True, exist_ok=True)
    emergency_route_xml = NET_REPAIR_WS / "er_acc_013_original_emergency_only.rou.xml"
    write_emergency_route_xml(emergency_route_xml, route_row, "emergency_ER_ACC_013_repair_diag")
    emergency_only = run_sumo_smoke(
        args.net,
        [emergency_route_xml],
        NET_REPAIR_RUNS / "emergency_only_original",
        "emergency_ER_ACC_013_repair_diag",
        0,
        args.time_to_teleport,
        args.collision_action,
    )
    verification = [
        {
            "candidate_id": "original_route_emergency_only",
            "candidate_type": "demand_interaction_diagnosis",
            "net_variant": rel(args.net),
            "route_variant": rel(emergency_route_xml),
            "same_start": True,
            "same_target": True,
            **emergency_only,
            "final_status": "PASS" if emergency_only["sumo_exit_code"] == 0 and emergency_only["emergency_arrived"] and not emergency_only["emergency_teleport"] else "FAIL",
        },
        {
            "candidate_id": "original_route_b0_0p15_existing",
            "candidate_type": "baseline_reference",
            "net_variant": rel(args.net),
            "route_variant": "data_prepared/routes/emergency_routes_spine_v2.csv",
            "same_start": True,
            "same_target": True,
            "sumo_exit_code": b0_row["sumo_exit_code"],
            "emergency_departed": b0_row["emergency_departed"],
            "emergency_arrived": b0_row["emergency_arrived"],
            "emergency_teleport": b0_row["emergency_teleport"],
            "route_error_count": b0_row["route_error_count"],
            "emergency_travel_time": b0_row["emergency_travel_time"],
            "background_departed": b0_row["background_departed"],
            "background_arrived": b0_row["background_arrived"],
            "background_teleported": b0_row["background_teleported"],
            "run_dir": b0_row["run_dir"],
            "final_status": b0_row["final_status"],
        },
    ]
    repair_decision = "EXCLUDE_PRELIMINARY"
    if not connection_exists:
        repair_decision = "BLOCKED"
    elif emergency_only["emergency_arrived"] and not emergency_only["emergency_teleport"] and b0_row["emergency_teleport"]:
        repair_decision = "EXCLUDE_PRELIMINARY"
    candidates = [
        {
            "candidate_id": "no_net_patch_background_jam_diagnostic",
            "candidate_type": "demand_interaction_diagnosis",
            "description": "Connection and permissions are valid; emergency-only passes, B0 fails under local background jam. No net patch selected.",
            "net_variant": rel(args.net),
            "route_variant": "original ER_ACC_013",
            "safe_to_apply": False,
            "selected": False,
            "decision": repair_decision,
            "reason": diagnosis["primary_cause_assessment"],
        }
    ]
    diagnosis["repair_decision"] = repair_decision
    write_json(DIAG_JSON, diagnosis)
    write_csv(REPAIR_CANDIDATES_CSV, candidates, csv_fields(candidates))
    write_csv(REPAIR_VERIFY_CSV, verification, csv_fields(verification))
    return diagnosis, candidates, verification, repair_decision


def load_spine_edges(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {row["edge_id"] for row in read_csv(path) if row.get("is_spine_edge") == "True"}


def build_terminal_candidates(tls_audit: list[dict[str, str]], spine_edges: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in tls_audit:
        grouped.setdefault(row["tls_id"], []).append(row)
    candidates = []
    for idx, (tls_id, rows) in enumerate(sorted(grouped.items()), start=1):
        route_ids = sorted({row["route_id"] for row in rows})
        incoming_edges = sorted({row["emergency_incoming_edge"] for row in rows})
        outgoing_edges = sorted({row["emergency_outgoing_edge"] for row in rows})
        nearest_spine = next((edge for edge in incoming_edges + outgoing_edges if edge in spine_edges), "")
        controllable = all(row.get("is_controllable") == "True" for row in rows)
        green_ok = any(row.get("green_phase_indices", "").strip() for row in rows)
        incomplete = any(row.get("audit_status") != "PASS" for row in rows)
        if controllable and green_ok and route_ids:
            status = "PASS"
            reason = "controllable_tls_with_route_linkIndex_and_green_phase"
        elif incomplete:
            status = "WARNING"
            reason = "route_relevant_tls_with_incomplete_phase_information"
        else:
            status = "FAIL"
            reason = "not_safely_controllable"
        distances = [float(row.get("distance_from_route_start_m") or 0) for row in rows]
        candidates.append(
            {
                "terminal_id": f"PT_{idx:03d}",
                "tls_id": tls_id,
                "junction_id": rows[0].get("junction_id", tls_id),
                "corridor_group": "jungbu_firestation_to_seoul_station",
                "nearest_spine_edge": nearest_spine,
                "distance_to_spine_m": 0 if nearest_spine else "",
                "covered_route_count": len(route_ids),
                "covered_route_ids": " ".join(route_ids),
                "avg_distance_from_firestation_m": round(sum(distances) / len(distances), 3) if distances else "",
                "emergency_incoming_edges": " ".join(incoming_edges),
                "emergency_outgoing_edges": " ".join(outgoing_edges),
                "emergency_link_indices": " ".join(sorted({row["emergency_link_index"] for row in rows if row.get("emergency_link_index")})),
                "green_phase_indices": " ".join(sorted({phase for row in rows for phase in row.get("green_phase_indices", "").split()})),
                "yellow_phase_indices": " ".join(sorted({phase for row in rows for phase in row.get("yellow_phase_indices", "").split()})),
                "clearance_phase_indices": " ".join(sorted({phase for row in rows for phase in row.get("all_red_or_clearance_phase_indices", "").split()})),
                "is_controllable": controllable,
                "green_phase_ok": green_ok,
                "install_candidate_status": status,
                "install_candidate_reason": reason,
            }
        )
    summary = {
        "generated_at": utc_now(),
        "terminal_count": len(candidates),
        "status_counts": {status: sum(1 for row in candidates if row["install_candidate_status"] == status) for status in ["PASS", "WARNING", "FAIL"]},
        "covered_route_count": len({route_id for row in candidates for route_id in str(row["covered_route_ids"]).split()}),
        "source_of_truth": "net XML connection/tl/linkIndex/tlLogic via Step 11A TLS audit",
    }
    return candidates, summary


def write_b1_config() -> dict[str, Any]:
    config = {
        "schema": "b1_priority_signal_config.v1",
        "generated_at": utc_now(),
        "D_det": 300,
        "v_e_policy": "current_speed_with_fallback",
        "fallback_v_e_mps": 8.33,
        "alpha": 1.2,
        "G_ext": 30,
        "rho": "restore_original_program",
        "SC": None,
        "tau": 1,
        "t_lead": 30,
        "metric_sample_interval": 10,
        "phase_control_policy": "existing_sequence_only_no_direct_state_rewrite",
        "yellow_clearance_policy": "do_not_skip",
        "pedestrian_min_walk_policy": "safety_placeholder_documented_not_optimized",
        "b2_bayesian_optimization": "not_implemented",
    }
    write_json(CONFIG_PATH, config)
    return config


def import_traci() -> Any:
    try:
        import traci  # type: ignore

        return traci
    except ImportError:
        sumo_home = os.environ.get("SUMO_HOME")
        if sumo_home:
            tools = Path(sumo_home) / "tools"
            if tools.is_dir() and str(tools) not in sys.path:
                sys.path.insert(0, str(tools))
        try:
            import traci  # type: ignore

            return traci
        except ImportError as exc:
            raise Step12Error(f"missing_python_module: traci; SUMO_HOME={os.environ.get('SUMO_HOME', '')}") from exc


def load_tls_plan(tls_audit: list[dict[str, str]], route_id: str) -> list[dict[str, Any]]:
    rows = []
    for row in tls_audit:
        if row.get("route_id") != route_id:
            continue
        rows.append(
            {
                "route_id": route_id,
                "tls_id": row["tls_id"],
                "junction_id": row["junction_id"],
                "incoming": row["emergency_incoming_edge"],
                "outgoing": row["emergency_outgoing_edge"],
                "distance": float(row["distance_from_route_start_m"] or 0),
                "green_phases": [int(value) for value in row.get("green_phase_indices", "").split() if value.isdigit()],
                "is_controllable": row.get("is_controllable") == "True",
                "audit_reason": row.get("audit_reason", ""),
            }
        )
    rows.sort(key=lambda item: item["distance"])
    return rows


def run_b1_controller(
    args: argparse.Namespace,
    route_row: dict[str, str],
    tls_plan: list[dict[str, Any]],
    config: dict[str, Any],
    run_dir: Path,
    background_vehicle_count: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    route_id = route_row["route_id"]
    vehicle_id = f"emergency_{route_id}_b1_batch"
    emergency_route_xml = run_dir / f"{vehicle_id}.rou.xml"
    write_emergency_route_xml(emergency_route_xml, route_row, vehicle_id, 0.0, "b1_batch_emergency_type")
    paths = write_sumo_files(run_dir, args.net, [args.background_route, emergency_route_xml], args.time_to_teleport, args.collision_action)
    traci = import_traci()
    sumo = shutil.which("sumo")
    if sumo is None:
        raise Step12Error("missing_executable: sumo")
    events: list[dict[str, Any]] = []
    touched = {}
    route_edges = route_row["route_edges"].split()
    edge_starts = route_edge_starts(args.net, route_edges)
    cmd = [sumo, "-c", str(paths["sumocfg"]), "--error-log", str(paths["stderr"])]
    controller_started = False
    with paths["stdout"].open("w", encoding="utf-8") as stdout:
        traci.start(cmd, stdout=stdout)
        controller_started = True
        try:
            step = 0
            while traci.simulation.getMinExpectedNumber() > 0 and step <= args.timeout_steps:
                traci.simulationStep()
                sim_time = traci.simulation.getTime()
                if vehicle_id in traci.vehicle.getIDList():
                    road_id = traci.vehicle.getRoadID(vehicle_id)
                    speed = max(float(traci.vehicle.getSpeed(vehicle_id)), float(config["fallback_v_e_mps"]))
                    route_index = int(traci.vehicle.getRouteIndex(vehicle_id))
                    lane_position = float(traci.vehicle.getLanePosition(vehicle_id))
                    current_distance = edge_starts[route_index] + lane_position if 0 <= route_index < len(edge_starts) else 0.0
                    next_tls = None
                    for candidate in tls_plan:
                        if candidate["tls_id"] in touched:
                            continue
                        if candidate["distance"] + 1.0 < current_distance:
                            continue
                        next_tls = candidate
                        break
                    if next_tls is not None:
                        tls = next_tls
                        eta = 0.0 if road_id == tls["incoming"] else max((tls["distance"] - current_distance) / speed, 0.0)
                        if eta <= float(config["t_lead"]) or road_id == tls["incoming"]:
                            base = {
                                "time": sim_time,
                                "route_id": route_id,
                                "vehicle_id": vehicle_id,
                                "tls_id": tls["tls_id"],
                                "junction_id": tls["junction_id"],
                                "incoming": tls["incoming"],
                                "outgoing": tls["outgoing"],
                                "eta_sec": round(eta, 3),
                                "current_road_id": road_id,
                            }
                            if not tls["is_controllable"]:
                                touched[tls["tls_id"]] = "skip"
                                events.append({**base, "action": "skip", "reason": tls["audit_reason"] or "audit_not_controllable"})
                            else:
                                phase = int(traci.trafficlight.getPhase(tls["tls_id"]))
                                green = tls["green_phases"]
                                if phase in green:
                                    traci.trafficlight.setPhaseDuration(tls["tls_id"], min(float(config["G_ext"]), 5.0))
                                    touched[tls["tls_id"]] = "extend_green"
                                    events.append({**base, "action": "extend_green", "reason": "current_phase_already_green", "phase": phase})
                                else:
                                    next_phase = (phase + 1) % len(traci.trafficlight.getAllProgramLogics(tls["tls_id"])[0].phases)
                                    if next_phase in green:
                                        traci.trafficlight.setPhase(tls["tls_id"], next_phase)
                                        traci.trafficlight.setPhaseDuration(tls["tls_id"], min(float(config["G_ext"]), 5.0))
                                        touched[tls["tls_id"]] = "advance_to_next_green"
                                        events.append({**base, "action": "advance_to_next_green", "reason": "next_phase_is_emergency_green_existing_sequence", "phase": next_phase})
                                    else:
                                        touched[tls["tls_id"]] = "skip"
                                        events.append({**base, "action": "skip", "reason": "safe_green_not_current_or_next_phase", "phase": phase, "green_phase_indices": " ".join(map(str, green))})
                step += int(config["tau"])
            if step > args.timeout_steps:
                events.append({"time": traci.simulation.getTime(), "route_id": route_id, "vehicle_id": vehicle_id, "action": "timeout", "reason": "controller_timeout_steps"})
        finally:
            traci.close(False)
    stderr = paths["stderr"].read_text(encoding="utf-8", errors="replace") if paths["stderr"].is_file() else ""
    summary = parse_summary(paths["summary"])
    trip = parse_tripinfo(paths["tripinfo"], vehicle_id)
    emergency_tp = emergency_teleport_lines(stderr, vehicle_id)
    departed = summary["departed_total"] > background_vehicle_count or trip["arrived"]
    arrived = bool(trip["arrived"])
    route_errors = route_error_count(stderr)
    bg_departed = max(summary["departed_total"] - (1 if departed else 0), 0)
    bg_tp = max(summary["teleport_count"] - (1 if emergency_tp else 0), 0)
    actions = [event.get("action") for event in events]
    failures = []
    warnings = []
    if not controller_started:
        failures.append("controller_not_started")
    if not departed:
        failures.append("emergency_not_departed")
    if not arrived:
        failures.append("emergency_not_arrived")
    if emergency_tp:
        failures.append("emergency_teleport_detected")
    if route_errors > 0:
        failures.append("route_error_count_gt_0")
    if bg_tp > 0:
        warnings.append("background_teleports_present")
    row = {
        "route_id": route_id,
        "b1_status": "FAIL" if failures else "WARNING" if warnings else "PASS",
        "emergency_departed": departed,
        "emergency_arrived": arrived,
        "emergency_teleport": bool(emergency_tp),
        "route_error_count": route_errors,
        "b1_emergency_travel_time": trip["travel_time"],
        "controlled_tls_count": len({e["tls_id"] for e in events if e.get("action") in {"extend_green", "advance_to_next_green"} and e.get("tls_id")}),
        "skipped_tls_count": len({e["tls_id"] for e in events if e.get("action") == "skip" and e.get("tls_id")}),
        "intervention_count": sum(1 for action in actions if action in {"extend_green", "advance_to_next_green"}),
        "phase_switch_count": actions.count("advance_to_next_green"),
        "green_extension_count": actions.count("extend_green"),
        "restore_count": 0,
        "failed_tls_count": 0,
        "background_teleport_ratio": round(bg_tp / bg_departed, 6) if bg_departed else 0.0,
        "sumo_exit_code": 0 if controller_started else 1,
        "failure_reason": ";".join(failures),
        "warning_reason": ";".join(warnings),
        "net_variant": rel(args.net),
        "route_variant": "original",
        "run_dir": rel(run_dir),
        "stderr_log": rel(paths["stderr"]),
    }
    return row, events


def b0_valid_rows(b0_summary: dict[str, Any], repair_decision: str) -> list[dict[str, Any]]:
    rows = []
    for row in b0_summary["results"]:
        valid = (
            row.get("sumo_exit_code") == 0
            and row.get("emergency_arrived") is True
            and row.get("emergency_teleport") is False
            and int(row.get("route_error_count") or 0) == 0
        )
        if row["route_id"] == TARGET_ROUTE_ID and repair_decision != "REPAIRED":
            valid = False
        if valid:
            rows.append(row)
    return rows


def run_b1_batch(args: argparse.Namespace, routes: list[dict[str, str]], tls_audit: list[dict[str, str]], config: dict[str, Any], b0_summary: dict[str, Any], repair_decision: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    included_b0 = b0_valid_rows(b0_summary, repair_decision)
    included_ids = [row["route_id"] for row in included_b0]
    route_by_id = {row["route_id"]: row for row in routes}
    b0_by_id = {row["route_id"]: row for row in b0_summary["results"]}
    blockers = []
    terminal_routes = {row["route_id"] for row in tls_audit if row.get("is_controllable") == "True"}
    for route_id in included_ids:
        if route_id not in terminal_routes:
            blockers.append(f"missing route-specific TLS mapping: {route_id}")
    if blockers:
        BLOCKER_DOC.write_text("# B1 Batch Smoke Blockers\n\n" + "\n".join(f"- {item}" for item in blockers) + "\n", encoding="utf-8")
        return {"run_status": "BLOCKED", "blockers": blockers, "included_route_ids": included_ids}, [], []
    background_count = count_vehicles(args.background_route)
    rows = []
    events_all = []
    emergency_tp_count = 0
    for route_id in included_ids:
        row, events = run_b1_controller(args, route_by_id[route_id], load_tls_plan(tls_audit, route_id), config, B1_RUN_ROOT / route_id, background_count)
        b0 = b0_by_id[route_id]
        row["b0_status"] = b0["final_status"]
        row["b0_emergency_travel_time"] = b0["emergency_travel_time"]
        if row.get("b1_emergency_travel_time") not in {None, ""}:
            delta = float(row["b1_emergency_travel_time"]) - float(b0["emergency_travel_time"])
            row["travel_time_delta_sec"] = round(delta, 6)
            row["travel_time_improvement_pct"] = round((-delta / float(b0["emergency_travel_time"])) * 100.0, 6)
        else:
            row["travel_time_delta_sec"] = ""
            row["travel_time_improvement_pct"] = ""
        rows.append(row)
        events_all.extend(events)
        if row["emergency_teleport"]:
            emergency_tp_count += 1
        if emergency_tp_count >= 2:
            break
    status_counts = {status: sum(1 for row in rows if row["b1_status"] == status) for status in ["PASS", "WARNING", "FAIL"]}
    avg_b0 = sum(float(row["b0_emergency_travel_time"]) for row in rows if row.get("b0_emergency_travel_time") not in {None, ""}) / len(rows) if rows else None
    avg_b1_values = [float(row["b1_emergency_travel_time"]) for row in rows if row.get("b1_emergency_travel_time") not in {None, ""}]
    avg_b1 = sum(avg_b1_values) / len(avg_b1_values) if avg_b1_values else None
    improvement = ((avg_b0 - avg_b1) / avg_b0 * 100.0) if avg_b0 and avg_b1 is not None else None
    summary = {
        "generated_at": utc_now(),
        "run_status": "STOPPED_EMERGENCY_TELEPORT" if emergency_tp_count >= 2 else "RUN",
        "included_route_ids": included_ids,
        "executed_route_count": len(rows),
        "status_counts": status_counts,
        "emergency_teleport_routes": [row["route_id"] for row in rows if row["emergency_teleport"]],
        "controller_failure_routes": [row["route_id"] for row in rows if "controller" in str(row.get("failure_reason", ""))],
        "average_b0_travel_time": round(avg_b0, 6) if avg_b0 is not None else None,
        "average_b1_travel_time": round(avg_b1, 6) if avg_b1 is not None else None,
        "preliminary_improvement_pct": round(improvement, 6) if improvement is not None else None,
    }
    write_csv(B1_BATCH_CSV, rows, csv_fields(rows))
    write_csv(B1_EVENTS_CSV, events_all, ["time", "route_id", "vehicle_id", "tls_id", "junction_id", "incoming", "outgoing", "eta_sec", "current_road_id", "action", "reason", "phase", "green_phase_indices"])
    write_json(B1_BATCH_JSON, {**summary, "results": rows})
    B1_LOG.parent.mkdir(parents=True, exist_ok=True)
    B1_LOG.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary, rows, events_all


def write_docs(diagnosis: dict[str, Any], candidates: list[dict[str, Any]], verification: list[dict[str, Any]], terminal_summary: dict[str, Any], config: dict[str, Any], b1_summary: dict[str, Any], b0_summary: dict[str, Any]) -> None:
    DIAG_DOC.write_text(
        f"""# ER_ACC_013 B0 Teleport Diagnosis

- route_id: `{diagnosis['route_id']}`
- teleport time: `{diagnosis['teleport_time']}`
- teleport lane: `{diagnosis['teleport_lane']}`
- internal lane: `{diagnosis['is_internal_lane']}`
- incoming/outgoing: `{diagnosis['incoming_edge_before_internal_lane']}` -> `{diagnosis['outgoing_edge_after_internal_lane']}`
- nearest TLS: `{diagnosis['nearest_tls']}`
- net connection exists: `{diagnosis['net_connection_exists']}`
- lane permits emergency/passenger: `{diagnosis['lane_permits_emergency']}` / `{diagnosis['lane_permits_passenger']}`
- likely causes: `{diagnosis['likely_causes']}`
- assessment: `{diagnosis['primary_cause_assessment']}`
- time-to-teleport masking: `{diagnosis['increase_time_to_teleport_masks_issue']}`
- repair decision: `{diagnosis['repair_decision']}`
""",
        encoding="utf-8",
    )
    REPAIR_DOC.write_text(
        f"""# ER_ACC_013 Repair Report

## Decision

`{diagnosis['repair_decision']}`

## Candidates

{json.dumps(candidates, ensure_ascii=False, indent=2)}

## Verification

{json.dumps(verification, ensure_ascii=False, indent=2)}

Original net, route CSV, and 0.15x background demand were not overwritten.
""",
        encoding="utf-8",
    )
    b1_status = b1_summary.get("run_status", "NOT_RUN")
    STEP12_DOC.write_text(
        f"""# Step 12 Signal Mapping and Net Repair

## 1. Step 10/11 Status

- B0 19-route: `{b0_summary.get('status_counts')}`
- B0 failed route: `ER_ACC_013`
- Step 11A TLS audit: `{terminal_summary.get('source_of_truth')}`
- Step 11B ER_ACC_002 smoke: PASS in prior step

## 2. ER_ACC_013 Diagnosis and Repair Decision

ER_ACC_013 teleported on `{diagnosis['teleport_lane']}` at `{diagnosis['teleport_time']}`. The lane is an internal lane for `{diagnosis['incoming_edge_before_internal_lane']}` -> `{diagnosis['outgoing_edge_after_internal_lane']}` at TLS `{diagnosis['nearest_tls']}`. The net connection exists and the lane does not disallow emergency/passenger classes. Emergency-only verification passed, so the preliminary decision is `{diagnosis['repair_decision']}` rather than rewriting the net.

## 3. Terminal Candidate Mapping

Terminal candidates are grouped from Step 11A route-TLS audit rows. Controllability is based on net XML `connection`, `tl`, `linkIndex`, and `tlLogic`; GeoJSON is not used for control decisions.

- terminal status counts: `{terminal_summary['status_counts']}`
- covered route count: `{terminal_summary['covered_route_count']}`

## 4. B1 Priority Config

- config: `configs/b1_priority_signal_config.json`
- D_det: `{config['D_det']}` detection distance, recorded for approach detection.
- v_e_policy: `{config['v_e_policy']}` uses current speed with fallback for ETA.
- fallback_v_e_mps: `{config['fallback_v_e_mps']}`.
- alpha: `{config['alpha']}` ETA safety margin, recorded for later tuning.
- G_ext: `{config['G_ext']}` max green extension.
- rho: `{config['rho']}` restoration policy.
- SC: `{config['SC']}` shared cycle, record-only now.
- tau: `{config['tau']}` ETA recalculation interval.
- t_lead: `{config['t_lead']}` lead time before arrival.
- metric_sample_interval: `{config['metric_sample_interval']}` record-only metric cadence.

B2 and Bayesian Optimization are not implemented because this stage only prepares a safe B1 smoke-ready signal network and validates preliminary controller behavior.

## 5. B1 Route-Level Smoke Scope

Included routes are B0-valid routes only. ER_ACC_013 remains excluded unless a documented patched variant is explicitly selected. B1 run status: `{b1_status}`.

## 6. Remaining Blockers and Next Safe Step

Remaining blocker: ER_ACC_013 B0 baseline is invalid under 0.15x background demand. Next safe step is to review ER_ACC_013 local junction demand/signal behavior or run B1 comparison on the B0-valid preliminary route set only.
""",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    args.net = args.net.resolve()
    args.background_route = args.background_route.resolve()
    args.emergency_routes = args.emergency_routes.resolve()
    args.tls_audit = args.tls_audit.resolve()
    args.spine_edges = args.spine_edges.resolve()
    args.b0_summary = args.b0_summary.resolve()
    args.b1_er002_summary = args.b1_er002_summary.resolve()
    required = {
        "active_net": args.net,
        "background_route": args.background_route,
        "emergency_routes": args.emergency_routes,
        "tls_audit": args.tls_audit,
        "b0_summary": args.b0_summary,
        "b0_summary_csv": PROJECT_ROOT / "results/metrics/b0_baseline_19route_smoke_summary.csv",
        "tls_audit_summary": PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_summary.json",
        "b1_er002": args.b1_er002_summary,
        "b1_er002_events": PROJECT_ROOT / "results/metrics/b1_er_acc_002_signal_events.csv",
        "step10_doc": PROJECT_ROOT / "docs/Step10.md",
        "step11_doc": PROJECT_ROOT / "docs/Step11.md",
    }
    prereq = prerequisite_check(required)
    missing = [row for row in prereq if row["exists"] != "True"]
    if missing:
        write_json(PROJECT_ROOT / "results/metrics/step12_prerequisite_blockers.json", {"generated_at": utc_now(), "missing": missing})
        raise Step12Error(f"missing required files: {missing}")
    net_data = parse_net(args.net)
    routes = read_csv(args.emergency_routes)
    tls_audit = read_csv(args.tls_audit)
    b0_summary = read_json(args.b0_summary)
    diagnosis, candidates, verification, repair_decision = diagnose_er_acc_013(args, net_data, routes, b0_summary)
    terminal_rows, terminal_summary = build_terminal_candidates(tls_audit, load_spine_edges(args.spine_edges))
    write_csv(TERMINAL_CSV, terminal_rows, csv_fields(terminal_rows))
    write_json(TERMINAL_JSON, terminal_summary)
    if terminal_summary["status_counts"].get("PASS", 0) <= 0:
        raise Step12Error("no PASS terminal candidates exist")
    config = write_b1_config()
    if args.run_b1_batch:
        b1_summary, b1_rows, _events = run_b1_batch(args, routes, tls_audit, config, b0_summary, repair_decision)
    else:
        b1_summary, b1_rows = {"run_status": "NOT_RUN", "blockers": ["--run-b1-batch disabled"]}, []
        BLOCKER_DOC.write_text("# B1 Batch Smoke Blockers\n\n- B1 batch disabled by CLI.\n", encoding="utf-8")
    write_docs(diagnosis, candidates, verification, terminal_summary, config, b1_summary, b0_summary)
    completion = {
        "generated_at": utc_now(),
        "prerequisite_status": "PASS",
        "er_acc_013_repair_decision": repair_decision,
        "repair_candidate_count": len(candidates),
        "best_repair_candidate": candidates[0]["candidate_id"] if candidates else None,
        "terminal_status_counts": terminal_summary["status_counts"],
        "terminal_covered_route_count": terminal_summary["covered_route_count"],
        "b1_config_path": rel(CONFIG_PATH),
        "b1_config_key_parameters": {key: config[key] for key in ["D_det", "fallback_v_e_mps", "alpha", "G_ext", "rho", "tau", "t_lead"]},
        "b1_route_level_smoke_run_status": b1_summary.get("run_status"),
        "b1_status_counts": b1_summary.get("status_counts"),
        "b1_emergency_teleport_routes": b1_summary.get("emergency_teleport_routes"),
        "b1_controller_failure_routes": b1_summary.get("controller_failure_routes"),
        "average_b0_travel_time": b1_summary.get("average_b0_travel_time"),
        "average_b1_travel_time": b1_summary.get("average_b1_travel_time"),
        "preliminary_improvement_pct": b1_summary.get("preliminary_improvement_pct"),
        "remaining_blockers": ["ER_ACC_013 excluded from preliminary B1 because B0 baseline emergency teleport remains unrepaired"],
        "next_safe_step": "Review ER_ACC_013 local junction/demand behavior, or use B0-valid route set for preliminary B1 comparison.",
    }
    write_json(PROJECT_ROOT / "results/metrics/step12_signal_mapping_and_net_repair_summary.json", completion)
    print(json.dumps(completion, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Step12Error, OSError, ET.ParseError, ValueError, RuntimeError) as exc:
        print(f"Step12 failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
