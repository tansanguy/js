#!/usr/bin/env python3
"""Run B0 emergency-only smoke checks for Step 7 routes."""

from __future__ import annotations

import csv
import argparse
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_NET = PROJECT_ROOT / "data_prepared/net/jungbu_ellipse_passenger.net.xml"
EMERGENCY_ROUTES_CSV = PROJECT_ROOT / "data_prepared/routes/emergency_routes.csv"
EMERGENCY_ROUTE_SUMMARY = PROJECT_ROOT / "data_prepared/routes/emergency_route_summary.json"
EMERGENCY_ROUTES_V2_CSV = PROJECT_ROOT / "data_prepared/routes/emergency_routes_spine_v2.csv"
EMERGENCY_ROUTE_V2_SUMMARY = PROJECT_ROOT / "data_prepared/routes/emergency_route_summary_spine_v2.json"
RUN_ROOT = PROJECT_ROOT / "runs/b0_emergency_only_smoke"
SUMMARY_CSV = PROJECT_ROOT / "results/metrics/b0_emergency_only_smoke_summary.csv"
SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b0_emergency_only_smoke_summary.json"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step08_b0_emergency_only_smoke.log"
SPINE_RUN_ROOT = PROJECT_ROOT / "runs/b0_emergency_only_smoke_spine"
SPINE_SUMMARY_CSV = PROJECT_ROOT / "results/metrics/b0_emergency_only_smoke_spine_summary.csv"
SPINE_SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b0_emergency_only_smoke_spine_summary.json"
SPINE_LOG_PATH = PROJECT_ROOT / "outputs/logs/step08_b0_emergency_only_smoke_spine.log"
SPINE_V2_RUN_ROOT = PROJECT_ROOT / "runs/b0_emergency_only_smoke_spine_v2"
SPINE_V2_SUMMARY_CSV = PROJECT_ROOT / "results/metrics/b0_emergency_only_smoke_spine_v2_summary.csv"
SPINE_V2_SUMMARY_JSON = PROJECT_ROOT / "results/metrics/b0_emergency_only_smoke_spine_v2_summary.json"
SPINE_V2_LOG_PATH = PROJECT_ROOT / "outputs/logs/step08_b0_emergency_only_smoke_spine_v2.log"


class Step08Error(RuntimeError):
    """Expected Step 8 smoke failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise Step08Error(f"JSON root must be object: {rel(path)}")
    return payload


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run B0 emergency-only smoke checks.")
    parser.add_argument(
        "--variant",
        choices=["default", "spine", "spine-v2"],
        default="default",
        help="Use default outputs or separate spine-corridor smoke outputs.",
    )
    return parser.parse_args()


def output_paths(variant: str) -> tuple[Path, Path, Path, Path, Path, Path, int]:
    if variant == "spine-v2":
        return SPINE_V2_RUN_ROOT, SPINE_V2_SUMMARY_CSV, SPINE_V2_SUMMARY_JSON, SPINE_V2_LOG_PATH, EMERGENCY_ROUTES_V2_CSV, EMERGENCY_ROUTE_V2_SUMMARY, 19
    if variant == "spine":
        return SPINE_RUN_ROOT, SPINE_SUMMARY_CSV, SPINE_SUMMARY_JSON, SPINE_LOG_PATH, EMERGENCY_ROUTES_CSV, EMERGENCY_ROUTE_SUMMARY, 20
    return RUN_ROOT, SUMMARY_CSV, SUMMARY_JSON, LOG_PATH, EMERGENCY_ROUTES_CSV, EMERGENCY_ROUTE_SUMMARY, 20


def write_smoke_route(path: Path, row: dict[str, str]) -> str:
    vehicle_id = f"veh_{row['route_id']}"
    root = ET.Element("routes")
    ET.SubElement(
        root,
        "vType",
        {
            "id": "emergency",
            "vClass": "emergency",
            "color": "1,0,0",
            "guiShape": "emergency",
        },
    )
    ET.SubElement(root, "route", {"id": row["route_id"], "edges": row["route_edges"]})
    ET.SubElement(
        root,
        "vehicle",
        {
            "id": vehicle_id,
            "type": "emergency",
            "route": row["route_id"],
            "depart": "0",
            "departLane": "best",
            "departSpeed": "max",
        },
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return vehicle_id


def write_sumocfg(path: Path, route_file: Path, tripinfo_file: Path, summary_file: Path) -> None:
    root = ET.Element("configuration")
    input_elem = ET.SubElement(root, "input")
    ET.SubElement(input_elem, "net-file", {"value": str(ACTIVE_NET)})
    ET.SubElement(input_elem, "route-files", {"value": str(route_file)})
    output_elem = ET.SubElement(root, "output")
    ET.SubElement(output_elem, "tripinfo-output", {"value": str(tripinfo_file)})
    ET.SubElement(output_elem, "summary-output", {"value": str(summary_file)})
    time_elem = ET.SubElement(root, "time")
    ET.SubElement(time_elem, "begin", {"value": "0"})
    report_elem = ET.SubElement(root, "report")
    ET.SubElement(report_elem, "no-step-log", {"value": "true"})
    ET.SubElement(report_elem, "duration-log.disable", {"value": "true"})
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def parse_tripinfo(path: Path, vehicle_id: str) -> tuple[bool, str, str]:
    if not path.is_file():
        return False, "", "tripinfo_missing"
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return False, "", f"tripinfo_parse_error:{exc}"
    for tripinfo in root.findall("tripinfo"):
        if tripinfo.get("id") == vehicle_id:
            return True, tripinfo.get("duration", ""), ""
    return False, "", "vehicle_tripinfo_missing"


def main() -> int:
    args = parse_args()
    run_root, summary_csv, summary_json, log_path, routes_csv, route_summary_path, expected_count = output_paths(args.variant)
    generated_at = utc_now()
    lines = ["Step 8 B0 emergency-only smoke", "===============================", f"generated_at: {generated_at}", f"variant: {args.variant}"]
    try:
        for path in [ACTIVE_NET, routes_csv, route_summary_path]:
            if not path.is_file():
                raise Step08Error(f"Required input missing: {rel(path)}")
        route_summary = load_json(route_summary_path)
        if route_summary.get("final_status") not in {"PASS", "WARNING"}:
            raise Step08Error(f"Step 7 summary blocks smoke: {route_summary.get('final_status')}")
        sumo = shutil.which("sumo")
        if sumo is None:
            raise Step08Error("sumo executable not found")

        rows = read_csv(routes_csv)
        if len(rows) != expected_count:
            raise Step08Error(f"Expected {expected_count} emergency routes, got {len(rows)}")

        results: list[dict[str, Any]] = []
        for row in rows:
            run_dir = run_root / row["scenario_id"]
            route_file = run_dir / "emergency_only.rou.xml"
            sumocfg = run_dir / "scenario.sumocfg"
            tripinfo = run_dir / "tripinfo.xml"
            summary = run_dir / "summary.xml"
            stdout_log = run_dir / "sumo_stdout.log"
            stderr_log = run_dir / "sumo_stderr.log"
            vehicle_id = write_smoke_route(route_file, row)
            write_sumocfg(sumocfg, route_file, tripinfo, summary)
            completed = subprocess.run(
                [sumo, "-c", str(sumocfg)],
                cwd=PROJECT_ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            stdout_log.write_text(completed.stdout, encoding="utf-8")
            stderr_log.write_text(completed.stderr, encoding="utf-8")
            arrived, travel_time, failure_reason = parse_tripinfo(tripinfo, vehicle_id)
            if completed.returncode != 0:
                failure_reason = failure_reason or f"sumo_exit_code_{completed.returncode}"
            results.append(
                {
                    "scenario_id": row["scenario_id"],
                    "route_id": row["route_id"],
                    "target_edge_id": row["target_edge_id"],
                    "exit_code": completed.returncode,
                    "arrived": arrived,
                    "travel_time": travel_time,
                    "failure_reason": failure_reason,
                    "run_dir": rel(run_dir),
                    "sumocfg": rel(sumocfg),
                    "tripinfo": rel(tripinfo),
                }
            )

        failed = [row for row in results if int(row["exit_code"]) != 0 or not row["arrived"]]
        final_status = "PASS" if not failed else "FAIL"
        fields = ["scenario_id", "route_id", "target_edge_id", "exit_code", "arrived", "travel_time", "failure_reason", "run_dir", "sumocfg", "tripinfo"]
        write_csv(summary_csv, results, fields)
        write_json(
            summary_json,
            {
                "generated_at": generated_at,
                "final_status": final_status,
                "active_net": rel(ACTIVE_NET),
                "routes_csv": rel(routes_csv),
                "variant": args.variant,
                "run_count": len(results),
                "arrived_count": sum(1 for row in results if row["arrived"]),
                "failed_count": len(failed),
                "results": results,
            },
        )
        lines.extend(
            [
                f"run_count: {len(results)}",
                f"arrived_count: {sum(1 for row in results if row['arrived'])}",
                f"failed_count: {len(failed)}",
                f"final_status: {final_status}",
                f"summary_csv: {rel(summary_csv)}",
                f"summary_json: {rel(summary_json)}",
            ]
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0 if final_status == "PASS" else 1
    except (Step08Error, OSError, ET.ParseError, subprocess.TimeoutExpired, ValueError) as exc:
        lines.extend(["Status: FAIL", str(exc)])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
