#!/usr/bin/env python3
"""Validate Step 6 selected edges and reduced-map route connectivity."""

from __future__ import annotations

import csv
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.net_utils import read_sumo_net  # noqa: E402


ACTIVE_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
ACTIVE_EDGES_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_edges.geojson"
ACTIVE_TLS_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_tls.geojson"
ACTIVE_REVIEW_HTML = "results/html/map_review_ellipse_passenger.html"
START_EDGE_ID = "-381802881#2"
SHORT_EDGE_THRESHOLD_M = 10.0
MIN_REACHABLE_CANDIDATES = 3

ACCIDENT_CANDIDATE_EDGES = [
    "-1099004169",
    "-176728951#4",
    "-198564930",
    "-198691090",
    "-228795481#0",
    "-228796091#1",
    "-272263606",
    "-37928420#2",
    "-420383159#1",
    "117784418#0",
    "1205679418#11",
    "170403814#0",
    "198564929#5",
    "198691079#0",
    "243542156#1",
    "273028084#6",
    "273640070#0",
    "301285277#0",
    "315658884#1",
    "381802879#1",
]

STATION_START_EDGE_JSON = PROJECT_ROOT / "data_prepared/routes/station_start_edge.json"
SELECTED_EDGES_JSON = PROJECT_ROOT / "data_prepared/manual/selected_edges.json"
SELECTED_VALIDATION_JSON = PROJECT_ROOT / "data_prepared/manual/selected_edges_validation.json"
ACCIDENT_CANDIDATES_CSV = PROJECT_ROOT / "data_prepared/manual/accident_candidate_edges.csv"
ROUTE_CHECK_CSV = PROJECT_ROOT / "data_prepared/routes/route_connectivity_check.csv"
REACHABLE_CANDIDATES_CSV = PROJECT_ROOT / "data_prepared/routes/reachable_accident_candidates.csv"
ROUTE_SUMMARY_JSON = PROJECT_ROOT / "data_prepared/routes/route_connectivity_summary.json"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step06_route_connectivity.log"


class Step06Error(RuntimeError):
    """Expected Step 6 failure with user-facing error text."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise Step06Error(f"ERROR: JSON root must be object: {rel(path)}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def backup_if_exists(path: Path, generated_at: str) -> str | None:
    if not path.exists():
        return None
    stamp = generated_at.replace(":", "").replace("-", "").split(".")[0]
    backup_path = path.with_name(f"{path.stem}.backup_{stamp}{path.suffix}")
    shutil.copy2(path, backup_path)
    return rel(backup_path)


def load_edge_geojson_index(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(path)
    if payload.get("type") != "FeatureCollection":
        raise Step06Error(f"ERROR: invalid GeoJSON FeatureCollection: {rel(path)}")
    features = payload.get("features")
    if not isinstance(features, list):
        raise Step06Error(f"ERROR: GeoJSON features must be a list: {rel(path)}")

    index: dict[str, dict[str, Any]] = {}
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties", {})
        if not isinstance(props, dict):
            continue
        edge_id = props.get("edge_id")
        if isinstance(edge_id, str):
            index[edge_id] = props
    return index


def edge_from_net(sumo_net: Any, edge_id: str) -> Any | None:
    try:
        return sumo_net.getEdge(edge_id)
    except Exception:  # noqa: BLE001 - sumolib raises generic exceptions for missing edge IDs.
        return None


def edge_bool(props: dict[str, Any], key: str) -> bool | None:
    value = props.get(key)
    return value if isinstance(value, bool) else None


def edge_float(props: dict[str, Any], key: str) -> float | None:
    value = props.get(key)
    if isinstance(value, int | float):
        return float(value)
    return None


def edge_int(props: dict[str, Any], key: str) -> int | None:
    value = props.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def validate_edge(
    edge_id: str,
    geojson_index: dict[str, dict[str, Any]],
    sumo_net: Any,
    duplicate_count: int = 1,
) -> dict[str, Any]:
    props = geojson_index.get(edge_id, {})
    net_edge = edge_from_net(sumo_net, edge_id)
    warnings: list[str] = []
    failures: list[str] = []

    exists_in_geojson = edge_id in geojson_index
    exists_in_net = net_edge is not None
    allows_passenger = edge_bool(props, "allows_passenger")
    is_internal = edge_bool(props, "is_internal")
    length_m = edge_float(props, "length_m")

    if not exists_in_geojson:
        failures.append("missing_from_reduced_edge_geojson")
    if not exists_in_net:
        failures.append("missing_from_reduced_net")
    if duplicate_count > 1:
        failures.append("duplicate_candidate_edge")
    if exists_in_geojson and allows_passenger is not True:
        failures.append("allows_passenger_not_true")
    if exists_in_geojson and is_internal is not False:
        failures.append("is_internal_not_false")
    if length_m is not None and length_m < SHORT_EDGE_THRESHOLD_M:
        warnings.append(f"short_edge_length_lt_{SHORT_EDGE_THRESHOLD_M:g}m")

    status = "FAIL" if failures else ("WARNING" if warnings else "PASS")
    return {
        "edge_id": edge_id,
        "exists_in_geojson": exists_in_geojson,
        "exists_in_net": exists_in_net,
        "allows_passenger": allows_passenger,
        "is_internal": is_internal,
        "length_m": length_m,
        "lane_count": edge_int(props, "lane_count"),
        "speed_mps": edge_float(props, "speed_mps"),
        "duplicate_count": duplicate_count,
        "status": status,
        "warnings": ";".join(warnings),
        "failure_reason": ";".join(failures),
    }


def route_tls_ids(route_edges: list[Any]) -> tuple[set[str], list[str]]:
    tls_ids: set[str] = set()
    warnings: list[str] = []
    for from_edge, to_edge in zip(route_edges, route_edges[1:], strict=False):
        outgoing = from_edge.getOutgoing()
        connections = outgoing.get(to_edge)
        if connections is None:
            warnings.append(f"missing_connection:{from_edge.getID()}->{to_edge.getID()}")
            continue
        for connection in connections:
            tls_id = connection.getTLSID()
            link_index = connection.getTLLinkIndex()
            if tls_id and link_index >= 0:
                tls_ids.add(tls_id)
    return tls_ids, warnings


def compute_route(sumo_net: Any, start_edge_id: str, target_edge_id: str) -> dict[str, Any]:
    start_edge = edge_from_net(sumo_net, start_edge_id)
    target_edge = edge_from_net(sumo_net, target_edge_id)
    if start_edge is None:
        return {"route_status": "FAIL", "failure_reason": "start_edge_missing_from_reduced_net"}
    if target_edge is None:
        return {"route_status": "FAIL", "failure_reason": "target_edge_missing_from_reduced_net"}

    try:
        route_edges, route_length_m = sumo_net.getOptimalPath(
            start_edge,
            target_edge,
            vClass="passenger",
            withInternal=False,
            includeFromToCost=True,
        )
    except Exception as exc:  # noqa: BLE001 - sumolib path errors are not strongly typed.
        return {"route_status": "FAIL", "failure_reason": f"sumolib_route_error:{exc}"}

    if not route_edges:
        return {"route_status": "FAIL", "failure_reason": "no_passenger_route_found"}

    route_edge_ids = [edge.getID() for edge in route_edges]
    tls_ids, tls_warnings = route_tls_ids(route_edges)
    return {
        "route_status": "WARNING" if tls_warnings else "PASS",
        "failure_reason": "",
        "route_edge_sequence": route_edge_ids,
        "route_edge_count": len(route_edge_ids),
        "route_length_m": round(float(route_length_m), 3),
        "route_tls_count": len(tls_ids),
        "route_tls_ids": sorted(tls_ids),
        "warnings": ";".join(tls_warnings),
    }


def merge_warnings(*parts: Any) -> str:
    warnings: list[str] = []
    for part in parts:
        if isinstance(part, str) and part:
            warnings.extend(item for item in part.split(";") if item)
    return ";".join(warnings)


def main() -> int:
    generated_at = utc_now()
    lines = [
        "Step 6 route connectivity validation",
        "====================================",
        f"generated_at: {generated_at}",
        f"active_net: {rel(ACTIVE_NET)}",
        f"active_edges_geojson: {rel(ACTIVE_EDGES_GEOJSON)}",
        f"active_tls_geojson: {rel(ACTIVE_TLS_GEOJSON)}",
        f"active_review_html: {ACTIVE_REVIEW_HTML}",
        f"station_start_edge_id: {START_EDGE_ID}",
    ]

    try:
        for path in [ACTIVE_NET, ACTIVE_EDGES_GEOJSON, ACTIVE_TLS_GEOJSON]:
            if not path.is_file():
                raise Step06Error(f"ERROR: active input missing: {rel(path)}")

        sumo_net = read_sumo_net(ACTIVE_NET)
        geojson_index = load_edge_geojson_index(ACTIVE_EDGES_GEOJSON)
        duplicate_counts = Counter(ACCIDENT_CANDIDATE_EDGES)

        start_validation = validate_edge(START_EDGE_ID, geojson_index, sumo_net)
        candidate_rows = [
            validate_edge(edge_id, geojson_index, sumo_net, duplicate_counts[edge_id])
            for edge_id in ACCIDENT_CANDIDATE_EDGES
        ]

        route_rows: list[dict[str, Any]] = []
        for row in candidate_rows:
            if start_validation["status"] == "FAIL" or row["status"] == "FAIL":
                route = {
                    "route_status": "FAIL",
                    "failure_reason": row["failure_reason"] or start_validation["failure_reason"],
                    "route_edge_sequence": [],
                    "route_edge_count": 0,
                    "route_length_m": "",
                    "route_tls_count": "",
                    "route_tls_ids": [],
                    "warnings": "",
                }
            else:
                route = compute_route(sumo_net, START_EDGE_ID, row["edge_id"])
            route_rows.append(
                {
                    **row,
                    "target_edge_id": row["edge_id"],
                    "route_status": route.get("route_status", "FAIL"),
                    "route_edge_count": route.get("route_edge_count", 0),
                    "route_length_m": route.get("route_length_m", ""),
                    "route_tls_count": route.get("route_tls_count", ""),
                    "route_tls_ids": ";".join(route.get("route_tls_ids", [])),
                    "route_edge_sequence": " ".join(route.get("route_edge_sequence", [])),
                    "warnings": merge_warnings(row.get("warnings"), route.get("warnings")),
                    "failure_reason": route.get("failure_reason") or row.get("failure_reason", ""),
                }
            )

        reachable_rows = [row for row in route_rows if row["route_status"] in {"PASS", "WARNING"}]
        unreachable_rows = [row for row in route_rows if row["route_status"] == "FAIL"]
        validation_failures = [row for row in [start_validation, *candidate_rows] if row["status"] == "FAIL"]
        validation_warnings = [row for row in [start_validation, *candidate_rows] if row["status"] == "WARNING"]
        route_warnings = [row for row in route_rows if row["route_status"] == "WARNING" or row["warnings"]]

        selected_backup = backup_if_exists(SELECTED_EDGES_JSON, generated_at)
        selected_payload = {
            "analysis_edges": [],
            "accident_candidate_edges": ACCIDENT_CANDIDATE_EDGES,
            "excluded_edges": [],
            "created_from": ACTIVE_REVIEW_HTML,
            "notes": "canonical Step 6 accident candidate edge input from reduced map manual selection",
        }

        selected_final_status = (
            "FAIL" if validation_failures else ("WARNING" if validation_warnings else "PASS")
        )
        route_final_status = (
            "FAIL"
            if validation_failures or len(reachable_rows) < MIN_REACHABLE_CANDIDATES
            else ("WARNING" if unreachable_rows or route_warnings else "PASS")
        )
        success_candidate = len(reachable_rows) >= MIN_REACHABLE_CANDIDATES and not validation_failures

        selected_validation = {
            "generated_at": generated_at,
            "final_status": selected_final_status,
            "active_map": {
                "net_file": rel(ACTIVE_NET),
                "edges_geojson": rel(ACTIVE_EDGES_GEOJSON),
                "tls_geojson": rel(ACTIVE_TLS_GEOJSON),
                "review_html": ACTIVE_REVIEW_HTML,
            },
            "station_start_edge": start_validation,
            "candidate_count": len(ACCIDENT_CANDIDATE_EDGES),
            "duplicate_edge_count": sum(count - 1 for count in duplicate_counts.values() if count > 1),
            "pass_count": sum(1 for row in candidate_rows if row["status"] == "PASS"),
            "warning_count": len(validation_warnings),
            "fail_count": len(validation_failures),
            "short_edge_threshold_m": SHORT_EDGE_THRESHOLD_M,
            "selected_edges_backup": selected_backup,
            "accident_candidate_edges": candidate_rows,
        }

        route_summary = {
            "generated_at": generated_at,
            "final_status": route_final_status,
            "success_candidate": success_candidate,
            "success_criteria": f"reachable_count >= {MIN_REACHABLE_CANDIDATES}",
            "active_net": rel(ACTIVE_NET),
            "start_edge_id": START_EDGE_ID,
            "candidate_count": len(ACCIDENT_CANDIDATE_EDGES),
            "reachable_count": len(reachable_rows),
            "unreachable_count": len(unreachable_rows),
            "route_warning_count": len(route_warnings),
            "validation_fail_count": len(validation_failures),
            "reachable_edges": [row["target_edge_id"] for row in reachable_rows],
            "unreachable_edges": [
                {"edge_id": row["target_edge_id"], "failure_reason": row["failure_reason"]}
                for row in unreachable_rows
            ],
        }

        station_payload = {
            "generated_at": generated_at,
            "station_start_edge_id": START_EDGE_ID,
            "active_net": rel(ACTIVE_NET),
            "active_edges_geojson": rel(ACTIVE_EDGES_GEOJSON),
            "validation": start_validation,
        }

        write_json(STATION_START_EDGE_JSON, station_payload)
        write_json(SELECTED_EDGES_JSON, selected_payload)
        write_json(SELECTED_VALIDATION_JSON, selected_validation)
        write_json(ROUTE_SUMMARY_JSON, route_summary)

        candidate_fields = [
            "edge_id",
            "exists_in_geojson",
            "exists_in_net",
            "allows_passenger",
            "is_internal",
            "length_m",
            "lane_count",
            "speed_mps",
            "duplicate_count",
            "status",
            "warnings",
            "failure_reason",
        ]
        route_fields = [
            "target_edge_id",
            "exists_in_geojson",
            "exists_in_net",
            "allows_passenger",
            "is_internal",
            "length_m",
            "lane_count",
            "speed_mps",
            "duplicate_count",
            "status",
            "route_status",
            "route_edge_count",
            "route_length_m",
            "route_tls_count",
            "route_tls_ids",
            "warnings",
            "failure_reason",
            "route_edge_sequence",
        ]
        reachable_fields = [
            "target_edge_id",
            "length_m",
            "lane_count",
            "speed_mps",
            "route_status",
            "route_edge_count",
            "route_length_m",
            "route_tls_count",
            "route_tls_ids",
            "warnings",
            "route_edge_sequence",
        ]
        write_csv(ACCIDENT_CANDIDATES_CSV, candidate_rows, candidate_fields)
        write_csv(ROUTE_CHECK_CSV, route_rows, route_fields)
        write_csv(REACHABLE_CANDIDATES_CSV, reachable_rows, reachable_fields)

        lines.extend(
            [
                f"selected_edges_validation_final_status: {selected_final_status}",
                f"route_connectivity_final_status: {route_final_status}",
                f"success_candidate: {success_candidate}",
                f"candidate_count: {len(ACCIDENT_CANDIDATE_EDGES)}",
                f"reachable_count: {len(reachable_rows)}",
                f"unreachable_count: {len(unreachable_rows)}",
                f"validation_fail_count: {len(validation_failures)}",
                f"route_warning_count: {len(route_warnings)}",
                f"selected_edges_backup: {selected_backup or 'none'}",
                "outputs:",
                f"- {rel(STATION_START_EDGE_JSON)}",
                f"- {rel(SELECTED_EDGES_JSON)}",
                f"- {rel(SELECTED_VALIDATION_JSON)}",
                f"- {rel(ACCIDENT_CANDIDATES_CSV)}",
                f"- {rel(ROUTE_CHECK_CSV)}",
                f"- {rel(REACHABLE_CANDIDATES_CSV)}",
                f"- {rel(ROUTE_SUMMARY_JSON)}",
                f"- {rel(LOG_PATH)}",
            ]
        )
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if route_final_status in {"PASS", "WARNING"} else 1
    except (Step06Error, OSError, ImportError, RuntimeError, ValueError) as exc:
        lines.extend(["Status: FAIL", str(exc)])
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
