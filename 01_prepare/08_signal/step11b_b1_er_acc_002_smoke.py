#!/usr/bin/env python3
"""Run a minimal Step 11B B1 controller smoke for ER_ACC_002."""

from __future__ import annotations

import argparse
import csv
import json
import os
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
DEFAULT_BACKGROUND_ROUTE = PROJECT_ROOT / "data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml"
DEFAULT_EMERGENCY_ROUTES = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
DEFAULT_TLS_AUDIT = PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_spine_v2.csv"
DEFAULT_RUN_DIR = PROJECT_ROOT / "runs/b1_er_acc_002_smoke"
SUMMARY_CSV = PROJECT_ROOT / "results/metrics/b1_er_acc_002_smoke_summary.csv"
SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b1_er_acc_002_smoke_summary.json"
SIGNAL_EVENTS_CSV = PROJECT_ROOT / "results/metrics/b1_er_acc_002_signal_events.csv"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step11b_b1_er_acc_002_smoke.log"
STEP11_DOC = PROJECT_ROOT / "docs/Step11.md"
DEFAULT_TIMEOUT_STEPS = 7200


class B1SmokeError(RuntimeError):
    """Expected B1 smoke failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ER_ACC_002 B1 controller smoke.")
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--background-route", type=Path, default=DEFAULT_BACKGROUND_ROUTE)
    parser.add_argument("--emergency-routes", type=Path, default=DEFAULT_EMERGENCY_ROUTES)
    parser.add_argument("--route-id", default="ER_ACC_002")
    parser.add_argument("--tls-audit", type=Path, default=DEFAULT_TLS_AUDIT)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--time-to-teleport", type=int, default=1200)
    parser.add_argument("--collision-action", choices=["none", "warn", "teleport", "remove"], default="warn")
    parser.add_argument("--emergency-depart", type=float, default=0.0)
    parser.add_argument("--emergency-vehicle-id", default="emergency_ER_ACC_002_b1")
    parser.add_argument("--poll-interval", type=int, default=1)
    parser.add_argument("--t-lead", type=float, default=30.0)
    parser.add_argument("--min-green-extension", type=float, default=5.0)
    parser.add_argument("--max-extension-per-tls", type=float, default=30.0)
    parser.add_argument("--timeout-steps", type=int, default=DEFAULT_TIMEOUT_STEPS)
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


def parse_xml_with_retry(path: Path, attempts: int = 5, delay_sec: float = 0.5) -> ET.ElementTree:
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


def select_emergency_route(path: Path, route_id: str) -> dict[str, str]:
    rows = read_csv(path)
    matches = [row for row in rows if row.get("route_id") == route_id]
    if not matches:
        raise B1SmokeError(f"Emergency route not found: {route_id}")
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


def write_emergency_route_xml(path: Path, route_row: dict[str, str], vehicle_id: str, depart: float) -> None:
    root = ET.Element("routes")
    vtype = ET.SubElement(
        root,
        "vType",
        {
            "id": "b1_emergency_type",
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
            "type": "b1_emergency_type",
            "route": route_row["route_id"],
            "depart": f"{depart:g}",
            "departLane": "best",
            "departSpeed": "max",
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_sumo_files(args: argparse.Namespace, emergency_route_xml: Path) -> dict[str, Path]:
    paths = {
        "additional": args.run_dir / "edge_data.add.xml",
        "edge_data": args.run_dir / "edgeData.xml",
        "sumocfg": args.run_dir / "scenario.sumocfg",
        "tripinfo": args.run_dir / "tripinfo.xml",
        "summary": args.run_dir / "summary.xml",
        "stdout": args.run_dir / "sumo_stdout.log",
        "stderr": args.run_dir / "sumo_stderr.log",
    }
    args.run_dir.mkdir(parents=True, exist_ok=True)
    additional = ET.Element("additional")
    ET.SubElement(
        additional,
        "edgeData",
        {
            "id": "b1_er_acc_002_edge_speed",
            "file": str(paths["edge_data"]),
            "begin": "0",
            "end": "86400",
            "freq": "86400",
            "excludeEmpty": "false",
        },
    )
    ET.ElementTree(additional).write(paths["additional"], encoding="utf-8", xml_declaration=True)
    config = ET.Element("configuration")
    input_elem = ET.SubElement(config, "input")
    ET.SubElement(input_elem, "net-file", {"value": str(args.net)})
    ET.SubElement(input_elem, "route-files", {"value": f"{args.background_route},{emergency_route_xml}"})
    ET.SubElement(input_elem, "additional-files", {"value": str(paths["additional"])})
    output_elem = ET.SubElement(config, "output")
    ET.SubElement(output_elem, "tripinfo-output", {"value": str(paths["tripinfo"])})
    ET.SubElement(output_elem, "summary-output", {"value": str(paths["summary"])})
    time_elem = ET.SubElement(config, "time")
    ET.SubElement(time_elem, "begin", {"value": "0"})
    processing_elem = ET.SubElement(config, "processing")
    ET.SubElement(processing_elem, "time-to-teleport", {"value": str(args.time_to_teleport)})
    ET.SubElement(processing_elem, "collision.action", {"value": args.collision_action})
    report_elem = ET.SubElement(config, "report")
    ET.SubElement(report_elem, "no-step-log", {"value": "true"})
    ET.SubElement(report_elem, "duration-log.disable", {"value": "true"})
    ET.ElementTree(config).write(paths["sumocfg"], encoding="utf-8", xml_declaration=True)
    return paths


def load_tls_plan(path: Path, route_id: str) -> list[dict[str, Any]]:
    rows = []
    for row in read_csv(path):
        if row.get("route_id") != route_id:
            continue
        green = [int(value) for value in row.get("green_phase_indices", "").split() if value.isdigit()]
        rows.append(
            {
                "route_id": row["route_id"],
                "tls_id": row["tls_id"],
                "junction_id": row["junction_id"],
                "incoming": row["emergency_incoming_edge"],
                "outgoing": row["emergency_outgoing_edge"],
                "distance": float(row["distance_from_route_start_m"] or 0),
                "link_index": int(row["emergency_link_index"]) if row.get("emergency_link_index", "").isdigit() else -1,
                "green_phases": green,
                "is_controllable": row.get("is_controllable") == "True",
                "audit_status": row.get("audit_status", ""),
                "audit_reason": row.get("audit_reason", ""),
            }
        )
    rows.sort(key=lambda item: item["distance"])
    return rows


def parse_summary_output(path: Path) -> dict[str, Any]:
    root = parse_xml_with_retry(path).getroot()
    last_step = None
    max_teleports = 0
    for step in root.findall("step"):
        last_step = step
        max_teleports = max(max_teleports, int(float(step.get("teleports", "0"))))
    if last_step is None:
        raise B1SmokeError(f"summary-output has no steps: {rel(path)}")
    return {
        "departed_count_total": int(float(last_step.get("inserted", "0"))),
        "arrived_count_total": int(float(last_step.get("arrived", "0"))),
        "teleport_count": max_teleports,
        "sim_end_time": float(last_step.get("time", "0")),
    }


def parse_tripinfo(path: Path, vehicle_id: str) -> dict[str, Any]:
    root = parse_xml_with_retry(path).getroot()
    for tripinfo in root.findall("tripinfo"):
        if tripinfo.get("id") == vehicle_id:
            return {
                "emergency_arrived": True,
                "emergency_travel_time": float(tripinfo.get("duration", "0")),
                "emergency_depart_time_observed": float(tripinfo.get("depart", "0")),
                "emergency_arrival_time": float(tripinfo.get("arrival", "0")),
            }
    return {
        "emergency_arrived": False,
        "emergency_travel_time": None,
        "emergency_depart_time_observed": None,
        "emergency_arrival_time": None,
    }


def route_error_count(stderr: str) -> int:
    lower = stderr.lower()
    return lower.count("route error") + lower.count("has no valid route") + lower.count("is not connected")


def emergency_teleport_lines(stderr: str, vehicle_id: str) -> list[str]:
    return [line for line in stderr.splitlines() if vehicle_id in line and "teleport" in line.lower()]


def route_edge_starts(net_path: Path, route_edges: list[str]) -> list[float]:
    sumo_net = read_sumo_net(str(net_path))
    starts = []
    cumulative = 0.0
    for edge_id in route_edges:
        starts.append(cumulative)
        cumulative += float(sumo_net.getEdge(edge_id).getLength())
    return starts


def append_step11_doc(summary: dict[str, Any]) -> None:
    current = STEP11_DOC.read_text(encoding="utf-8") if STEP11_DOC.is_file() else "# Step 11 Signal Priority Preparation\n"
    marker = "## Step 11B ER_ACC_002 B1 Smoke"
    base = current.split(marker)[0].rstrip()
    text = f"""{base}

{marker}

ER_ACC_002 단일 route에서 중앙형 closed-loop B1 controller smoke를 실행했다. 이 단계는 성능 개선이 아니라 controller 시작, TLS 감지, 개입/skip 로그 생성을 확인한다.

- final status: `{summary['final_status']}`
- controller started: `{summary['controller_started']}`
- emergency arrived/teleport: `{summary['emergency_arrived']}` / `{summary['emergency_teleport']}`
- emergency travel time: `{summary['emergency_travel_time']}`
- controlled TLS count: `{summary['controlled_tls_count']}`
- skipped TLS count: `{summary['skipped_tls_count']}`
- signal events: `results/metrics/b1_er_acc_002_signal_events.csv`

Safety placeholder: 보행자 최소 보행시간은 아직 독립 제약으로 완전 구현하지 않았다. 이번 smoke controller는 기존 SUMO phase sequence를 사용하고, yellow/clearance를 생략하지 않으며, 안전하게 처리할 수 없는 TLS는 skip한다.
"""
    STEP11_DOC.write_text(text + "\n", encoding="utf-8")


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
            raise B1SmokeError(f"missing_python_module: traci; SUMO_HOME={os.environ.get('SUMO_HOME', '')}") from exc


def run_controller(args: argparse.Namespace, paths: dict[str, Path], tls_plan: list[dict[str, Any]], route_edges: list[str]) -> tuple[list[dict[str, Any]], bool]:
    traci = import_traci()
    sumo = shutil.which("sumo")
    if sumo is None:
        raise B1SmokeError("missing_executable: sumo")
    events: list[dict[str, Any]] = []
    touched: dict[str, dict[str, Any]] = {}
    controlled_tls: set[str] = set()
    skipped_tls: set[str] = set()
    controller_started = False
    edge_starts = route_edge_starts(args.net, route_edges)
    cmd = [sumo, "-c", str(paths["sumocfg"]), "--error-log", str(paths["stderr"])]
    with paths["stdout"].open("w", encoding="utf-8") as stdout:
        traci.start(cmd, stdout=stdout)
        controller_started = True
        try:
            step = 0
            while traci.simulation.getMinExpectedNumber() > 0 and step <= args.timeout_steps:
                traci.simulationStep()
                sim_time = traci.simulation.getTime()
                if args.emergency_vehicle_id in traci.vehicle.getIDList():
                    road_id = traci.vehicle.getRoadID(args.emergency_vehicle_id)
                    speed = max(float(traci.vehicle.getSpeed(args.emergency_vehicle_id)), 5.56)
                    route_index = int(traci.vehicle.getRouteIndex(args.emergency_vehicle_id))
                    lane_position = float(traci.vehicle.getLanePosition(args.emergency_vehicle_id))
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
                        tls_id = tls["tls_id"]
                        eta = 0.0 if road_id == tls["incoming"] else max((tls["distance"] - current_distance) / speed, 0.0)
                        if eta > args.t_lead and road_id != tls["incoming"]:
                            step += args.poll_interval
                            continue
                        event_base = {
                            "time": sim_time,
                            "route_id": args.route_id,
                            "vehicle_id": args.emergency_vehicle_id,
                            "tls_id": tls_id,
                            "junction_id": tls["junction_id"],
                            "incoming": tls["incoming"],
                            "outgoing": tls["outgoing"],
                            "eta_sec": round(eta, 3),
                            "current_road_id": road_id,
                        }
                        if not tls["is_controllable"]:
                            skipped_tls.add(tls_id)
                            touched[tls_id] = {"action": "skip"}
                            events.append({**event_base, "action": "skip", "reason": tls["audit_reason"] or "audit_not_controllable"})
                        else:
                            current_phase = int(traci.trafficlight.getPhase(tls_id))
                            green_phases = tls["green_phases"]
                            if current_phase in green_phases:
                                traci.trafficlight.setPhaseDuration(tls_id, args.min_green_extension)
                                controlled_tls.add(tls_id)
                                touched[tls_id] = {"action": "extend_green"}
                                events.append({**event_base, "action": "extend_green", "reason": "current_phase_already_green", "phase": current_phase})
                            else:
                                next_phase = (current_phase + 1) % len(traci.trafficlight.getAllProgramLogics(tls_id)[0].phases)
                                if next_phase in green_phases:
                                    traci.trafficlight.setPhase(tls_id, next_phase)
                                    traci.trafficlight.setPhaseDuration(tls_id, args.min_green_extension)
                                    controlled_tls.add(tls_id)
                                    touched[tls_id] = {"action": "advance_to_next_green"}
                                    events.append(
                                        {
                                            **event_base,
                                            "action": "advance_to_next_green",
                                            "reason": "next_phase_is_emergency_green_existing_sequence",
                                            "phase": next_phase,
                                        }
                                    )
                                else:
                                    skipped_tls.add(tls_id)
                                    touched[tls_id] = {"action": "skip"}
                                    events.append(
                                        {
                                            **event_base,
                                            "action": "skip",
                                            "reason": "safe_green_not_current_or_next_phase",
                                            "phase": current_phase,
                                            "green_phase_indices": " ".join(map(str, green_phases)),
                                        }
                                    )
                step += args.poll_interval
            if step > args.timeout_steps:
                events.append({"time": traci.simulation.getTime(), "route_id": args.route_id, "action": "timeout", "reason": "controller_timeout_steps"})
        finally:
            traci.close(False)
    return events, controller_started


def main() -> int:
    args = parse_args()
    generated_at = utc_now()
    lines = ["Step 11B ER_ACC_002 B1 smoke", "==============================", f"generated_at: {generated_at}"]
    try:
        args.net = args.net.resolve()
        args.background_route = args.background_route.resolve()
        args.emergency_routes = args.emergency_routes.resolve()
        args.tls_audit = args.tls_audit.resolve()
        args.run_dir = args.run_dir.resolve()
        for path in [args.net, args.background_route, args.emergency_routes, args.tls_audit]:
            if not path.is_file():
                raise B1SmokeError(f"missing_file: {path}")
        route_row = select_emergency_route(args.emergency_routes, args.route_id)
        route_edges = route_row["route_edges"].split()
        validation_failures = validate_route_edges(args.net, route_edges)
        if validation_failures:
            raise B1SmokeError(f"emergency route validation failed: {';'.join(validation_failures[:10])}")
        tls_plan = load_tls_plan(args.tls_audit, args.route_id)
        if not tls_plan:
            raise B1SmokeError(f"no TLS audit rows for route_id={args.route_id}")
        emergency_route_xml = args.run_dir / f"{args.emergency_vehicle_id}.rou.xml"
        write_emergency_route_xml(emergency_route_xml, route_row, args.emergency_vehicle_id, args.emergency_depart)
        paths = write_sumo_files(args, emergency_route_xml)
        background_vehicle_count = count_vehicles(args.background_route)
        events, controller_started = run_controller(args, paths, tls_plan, route_edges)
        stderr_text = paths["stderr"].read_text(encoding="utf-8", errors="replace") if paths["stderr"].is_file() else ""
        summary_metrics = parse_summary_output(paths["summary"])
        trip = parse_tripinfo(paths["tripinfo"], args.emergency_vehicle_id)
        route_errors = route_error_count(stderr_text)
        emergency_tp = emergency_teleport_lines(stderr_text, args.emergency_vehicle_id)
        emergency_arrived = bool(trip["emergency_arrived"])
        emergency_departed = summary_metrics["departed_count_total"] > background_vehicle_count or emergency_arrived
        emergency_teleport = bool(emergency_tp)
        background_departed = max(summary_metrics["departed_count_total"] - (1 if emergency_departed else 0), 0)
        background_arrived = max(summary_metrics["arrived_count_total"] - (1 if emergency_arrived else 0), 0)
        background_teleported = max(summary_metrics["teleport_count"] - (1 if emergency_teleport else 0), 0)
        controlled_tls = {event["tls_id"] for event in events if event.get("action") in {"extend_green", "advance_to_next_green"} and event.get("tls_id")}
        skipped_tls = {event["tls_id"] for event in events if event.get("action") == "skip" and event.get("tls_id")}
        failures = []
        warnings = []
        if not controller_started:
            failures.append("controller_not_started")
        if not emergency_departed:
            failures.append("emergency_not_departed")
        if not emergency_arrived:
            failures.append("emergency_not_arrived")
        if emergency_teleport:
            failures.append("emergency_teleport_detected")
        if route_errors > 0:
            failures.append("route_error_count_gt_0")
        if not controlled_tls and not skipped_tls:
            failures.append("no_tls_decision_events")
        if background_teleported > 0:
            warnings.append("background_teleports_present")
        final_status = "FAIL" if failures else "WARNING" if warnings else "PASS"
        summary = {
            "generated_at": generated_at,
            "final_status": final_status,
            "active_net": rel(args.net),
            "background_route": rel(args.background_route),
            "background_vehicle_count": background_vehicle_count,
            "route_id": args.route_id,
            "emergency_vehicle_id": args.emergency_vehicle_id,
            "controller_started": controller_started,
            "emergency_departed": emergency_departed,
            "emergency_arrived": emergency_arrived,
            "emergency_teleport": emergency_teleport,
            "emergency_teleport_evidence": emergency_tp,
            "emergency_travel_time": trip["emergency_travel_time"],
            "background_departed": background_departed,
            "background_arrived": background_arrived,
            "background_teleported": background_teleported,
            "background_teleport_ratio": round(background_teleported / background_departed, 6) if background_departed else 0.0,
            "route_error_count": route_errors,
            "controlled_tls_count": len(controlled_tls),
            "skipped_tls_count": len(skipped_tls),
            "signal_event_count": len(events),
            "time_to_teleport": args.time_to_teleport,
            "collision_action": args.collision_action,
            "sim_end_time": summary_metrics["sim_end_time"],
            "sumo_exit_code": 0 if controller_started else 1,
            "warnings": warnings,
            "failures": failures,
            "run_dir": rel(args.run_dir),
            "sumocfg": rel(paths["sumocfg"]),
            "tripinfo": rel(paths["tripinfo"]),
            "summary_output": rel(paths["summary"]),
            "edgeData_output": rel(paths["edge_data"]),
            "stderr_log": rel(paths["stderr"]),
            "outputs": [rel(SUMMARY_CSV), rel(SUMMARY_JSON), rel(SIGNAL_EVENTS_CSV), rel(LOG_PATH), rel(STEP11_DOC)],
        }
        write_csv(SIGNAL_EVENTS_CSV, events, ["time", "route_id", "vehicle_id", "tls_id", "junction_id", "incoming", "outgoing", "eta_sec", "current_road_id", "action", "reason", "phase", "green_phase_indices"])
        write_csv(SUMMARY_CSV, [summary], list(summary.keys()))
        write_json(SUMMARY_JSON, summary)
        append_step11_doc(summary)
        lines.extend(
            [
                f"controller_started: {controller_started}",
                f"emergency_arrived: {emergency_arrived}",
                f"emergency_teleport: {emergency_teleport}",
                f"controlled_tls_count: {summary['controlled_tls_count']}",
                f"skipped_tls_count: {summary['skipped_tls_count']}",
                f"final_status: {final_status}",
                f"summary_json: {rel(SUMMARY_JSON)}",
            ]
        )
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if final_status in {"PASS", "WARNING"} else 1
    except (B1SmokeError, OSError, ET.ParseError, ValueError, RuntimeError) as exc:
        lines.extend(["final_status: FAIL", f"blocker: {exc}"])
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
