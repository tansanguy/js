#!/usr/bin/env python3
"""Build Step 11A TLS inventory and phase audit from the active SUMO net."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
DEFAULT_EMERGENCY_ROUTES = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
AUDIT_CSV = PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_spine_v2.csv"
AUDIT_JSON = PROJECT_ROOT / "data_prepared/signals/tls_phase_audit_summary.json"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step11a_tls_phase_audit.log"
STEP11_DOC = PROJECT_ROOT / "docs/Step11.md"


class TlsAuditError(RuntimeError):
    """Expected TLS audit failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit TLS phases for spine-v2 emergency routes.")
    parser.add_argument("--net", type=Path, default=DEFAULT_NET)
    parser.add_argument("--emergency-routes", type=Path, default=DEFAULT_EMERGENCY_ROUTES)
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


def csv_fields(rows: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    return fields


def parse_net(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    tl_logic: dict[str, dict[str, Any]] = {}
    for elem in root.findall("tlLogic"):
        tls_id = elem.get("id", "")
        phases = [
            {
                "index": index,
                "duration": float(phase.get("duration", "0") or 0),
                "state": phase.get("state", ""),
            }
            for index, phase in enumerate(elem.findall("phase"))
        ]
        tl_logic[tls_id] = {"program_id": elem.get("programID", ""), "type": elem.get("type", ""), "phases": phases}

    connections: dict[tuple[str, str], list[dict[str, str]]] = {}
    tls_connections = []
    for elem in root.findall("connection"):
        record = {
            "from": elem.get("from", ""),
            "to": elem.get("to", ""),
            "fromLane": elem.get("fromLane", ""),
            "toLane": elem.get("toLane", ""),
            "tl": elem.get("tl", ""),
            "linkIndex": elem.get("linkIndex", ""),
            "via": elem.get("via", ""),
        }
        connections.setdefault((record["from"], record["to"]), []).append(record)
        if record["tl"]:
            tls_connections.append(record)

    edges: dict[str, dict[str, Any]] = {}
    for elem in root.findall("edge"):
        edge_id = elem.get("id", "")
        if not edge_id or edge_id.startswith(":"):
            continue
        lanes = elem.findall("lane")
        length = float(lanes[0].get("length", "0") or 0) if lanes else 0.0
        edges[edge_id] = {"from": elem.get("from", ""), "to": elem.get("to", ""), "length": length}
    return {"tl_logic": tl_logic, "connections": connections, "tls_connections": tls_connections, "edges": edges}


def phase_indices(phases: list[dict[str, Any]], link_index: int) -> tuple[list[int], list[int], list[int], list[str]]:
    green = []
    yellow = []
    clearance = []
    reasons = []
    for phase in phases:
        state = phase["state"]
        index = phase["index"]
        if link_index >= len(state):
            reasons.append(f"phase_{index}_state_too_short")
            continue
        signal = state[link_index]
        if signal in {"G", "g"}:
            green.append(index)
        if signal == "y":
            yellow.append(index)
        if "G" not in state and "g" not in state:
            clearance.append(index)
    return green, yellow, clearance, reasons


def audit_route(route_row: dict[str, str], net_data: dict[str, Any]) -> list[dict[str, Any]]:
    edges = route_row["route_edges"].split()
    edge_meta = net_data["edges"]
    connections = net_data["connections"]
    tl_logic = net_data["tl_logic"]
    rows = []
    cumulative = 0.0
    seen = set()
    for incoming, outgoing in zip(edges, edges[1:], strict=False):
        matches = [record for record in connections.get((incoming, outgoing), []) if record.get("tl")]
        if matches:
            for record in matches:
                tls_id = record["tl"]
                link_text = record["linkIndex"]
                unique_key = (route_row["route_id"], incoming, outgoing, tls_id, link_text)
                if unique_key in seen:
                    continue
                seen.add(unique_key)
                program = tl_logic.get(tls_id)
                link_index = int(link_text) if link_text.isdigit() else -1
                green: list[int] = []
                yellow: list[int] = []
                clearance: list[int] = []
                phase_reasons: list[str] = []
                phase_count = 0
                phase_durations: list[float] = []
                phase_states: list[str] = []
                current_program_id = ""
                if program:
                    phases = program["phases"]
                    phase_count = len(phases)
                    phase_durations = [phase["duration"] for phase in phases]
                    phase_states = [phase["state"] for phase in phases]
                    current_program_id = program["program_id"]
                    if link_index >= 0:
                        green, yellow, clearance, phase_reasons = phase_indices(phases, link_index)
                audit_reasons = []
                if not program:
                    audit_reasons.append("missing_tlLogic")
                if link_index < 0:
                    audit_reasons.append("missing_or_invalid_linkIndex")
                if program and not green:
                    audit_reasons.append("no_green_phase_for_emergency_link")
                audit_reasons.extend(phase_reasons[:3])
                is_controllable = bool(program and link_index >= 0 and green and not phase_reasons)
                audit_status = "PASS" if is_controllable else "FAIL" if not program or link_index < 0 or not green else "WARNING"
                rows.append(
                    {
                        "route_id": route_row["route_id"],
                        "tls_id": tls_id,
                        "junction_id": edge_meta.get(incoming, {}).get("to", tls_id),
                        "emergency_incoming_edge": incoming,
                        "emergency_outgoing_edge": outgoing,
                        "distance_from_route_start_m": round(cumulative, 3),
                        "emergency_link_index": link_index if link_index >= 0 else "",
                        "green_phase_indices": " ".join(map(str, green)),
                        "yellow_phase_indices": " ".join(map(str, yellow)),
                        "all_red_or_clearance_phase_indices": " ".join(map(str, clearance)),
                        "current_program_id": current_program_id,
                        "phase_count": phase_count,
                        "phase_duration_list": " ".join(f"{duration:g}" for duration in phase_durations),
                        "phase_state_list": "|".join(phase_states),
                        "is_controllable": is_controllable,
                        "audit_status": audit_status,
                        "audit_reason": ";".join(audit_reasons) if audit_reasons else "ok",
                    }
                )
        cumulative += float(edge_meta.get(incoming, {}).get("length", 0.0))
    return rows


def write_step11_doc(summary: dict[str, Any]) -> None:
    text = f"""# Step 11 Signal Priority Preparation

## Step 11A TLS Inventory + Phase Audit

active reduced SUMO net XML을 source-of-truth로 사용해 spine-v2 emergency route가 통과하는 TLS connection, linkIndex, phase state를 audit했다. GeoJSON은 제어 가능성 판단에 사용하지 않는다.

- active net: `{summary['active_net']}`
- emergency routes: `{summary['emergency_routes']}`
- audited TLS rows: `{summary['audit_row_count']}`
- unique TLS count: `{summary['unique_tls_count']}`
- controllable rows: `{summary['controllable_row_count']}`
- status counts: `{summary['status_counts']}`
- audit CSV: `data_prepared/signals/tls_phase_audit_spine_v2.csv`
- audit JSON: `data_prepared/signals/tls_phase_audit_summary.json`

## Step 11B Safety Placeholder

B1 controller smoke는 ER_ACC_002 단일 route에서 controller 시작, TLS 감지, 제어/skip 판단 로그를 확인하는 단계다. 아직 보행자 최소 보행시간을 완전한 코드 제약으로 구현하지 못하면 controller는 기존 SUMO phase sequence와 yellow/clearance를 유지하고, 안전하게 green phase로 유도할 수 없는 TLS를 skip으로 기록해야 한다.
"""
    STEP11_DOC.parent.mkdir(parents=True, exist_ok=True)
    STEP11_DOC.write_text(text, encoding="utf-8")


def main() -> int:
    args = parse_args()
    generated_at = utc_now()
    lines = ["Step 11A TLS phase audit", "========================", f"generated_at: {generated_at}"]
    try:
        args.net = args.net.resolve()
        args.emergency_routes = args.emergency_routes.resolve()
        for path in [args.net, args.emergency_routes]:
            if not path.is_file():
                raise TlsAuditError(f"missing_file: {path}")
        net_data = parse_net(args.net)
        route_rows = read_csv(args.emergency_routes)
        if len(route_rows) != 19:
            raise TlsAuditError(f"expected 19 emergency routes, found {len(route_rows)}")
        audit_rows: list[dict[str, Any]] = []
        for route_row in route_rows:
            audit_rows.extend(audit_route(route_row, net_data))
        status_counts = {status: sum(1 for row in audit_rows if row["audit_status"] == status) for status in ["PASS", "WARNING", "FAIL"]}
        unique_tls = sorted({row["tls_id"] for row in audit_rows})
        controllable_rows = [row for row in audit_rows if row["is_controllable"] is True]
        summary = {
            "generated_at": generated_at,
            "final_status": "FAIL" if status_counts["FAIL"] else "WARNING" if status_counts["WARNING"] else "PASS",
            "active_net": rel(args.net),
            "emergency_routes": rel(args.emergency_routes),
            "net_tlLogic_count": len(net_data["tl_logic"]),
            "net_tls_connection_count": len(net_data["tls_connections"]),
            "route_count": len(route_rows),
            "audit_row_count": len(audit_rows),
            "unique_tls_count": len(unique_tls),
            "unique_tls_ids": unique_tls,
            "controllable_row_count": len(controllable_rows),
            "controllable_tls_count": len({row["tls_id"] for row in controllable_rows}),
            "status_counts": status_counts,
            "outputs": [rel(AUDIT_CSV), rel(AUDIT_JSON), rel(LOG_PATH), rel(STEP11_DOC)],
        }
        write_csv(AUDIT_CSV, audit_rows, csv_fields(audit_rows))
        write_json(AUDIT_JSON, summary)
        write_step11_doc(summary)
        lines.extend(
            [
                f"net_tlLogic_count: {summary['net_tlLogic_count']}",
                f"audit_row_count: {summary['audit_row_count']}",
                f"unique_tls_count: {summary['unique_tls_count']}",
                f"controllable_tls_count: {summary['controllable_tls_count']}",
                f"status_counts: {status_counts}",
                f"final_status: {summary['final_status']}",
                f"audit_json: {rel(AUDIT_JSON)}",
            ]
        )
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if summary["final_status"] in {"PASS", "WARNING"} else 1
    except (TlsAuditError, OSError, ET.ParseError, ValueError) as exc:
        lines.extend(["final_status: FAIL", f"blocker: {exc}"])
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
