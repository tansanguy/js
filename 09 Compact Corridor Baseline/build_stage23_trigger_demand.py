#!/usr/bin/env python3
"""Build a B04 demand variant that makes EVTSP Stage2/Stage3 observable.

The original B04 demand is preserved. This script appends a small number of
background passenger vehicles around the dispatch/Case-B windows and writes a
separate route file that can be selected with --background-route.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / "09 Compact Corridor Baseline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from b4_runtime import B4Stage1Inputs, DATA_ROOT, rel  # noqa: E402


DEFAULT_OUTPUT = PROJECT_ROOT / "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml"
DEFAULT_BASE_DEMAND = DATA_ROOT / "demand/background_routes_compact_v9_B04_ad_variance_smoothed.rou.xml"


def route_slice(route_edges: tuple[str, ...], start_edge: str, length: int) -> list[str]:
    if start_edge not in route_edges:
        raise ValueError(f"edge_not_in_stage1_route:{start_edge}")
    start = route_edges.index(start_edge)
    return list(route_edges[start : min(start + length, len(route_edges))])


def ensure_route(root: ET.Element, route_id: str, edges: list[str]) -> None:
    existing = root.find(f"./route[@id='{route_id}']")
    if existing is not None:
        existing.set("edges", " ".join(edges))
        return
    ET.SubElement(root, "route", {"id": route_id, "edges": " ".join(edges)})


def append_vehicle(
    root: ET.Element,
    *,
    vehicle_id: str,
    route_id: str,
    depart: float,
    vehicle_type: str = "b04_passenger",
) -> None:
    ET.SubElement(
        root,
        "vehicle",
        {
            "id": vehicle_id,
            "type": vehicle_type,
            "route": route_id,
            "depart": f"{depart:.2f}",
            "departLane": "best",
            "departPos": "base",
            "departSpeed": "0",
        },
    )


def sort_vehicle_elements_by_depart(root: ET.Element) -> None:
    non_vehicles = [child for child in list(root) if child.tag != "vehicle"]
    vehicles = [child for child in list(root) if child.tag == "vehicle"]
    vehicles.sort(key=lambda item: (float(item.get("depart", "0") or 0.0), item.get("id", "")))
    root[:] = [*non_vehicles, *vehicles]


def build_stage23_trigger_demand(
    *,
    base_demand: Path,
    output: Path,
    stage2_count: int,
    stage3_count: int,
    stage1: B4Stage1Inputs,
) -> dict[str, Any]:
    tree = ET.parse(base_demand)
    root = tree.getroot()
    if root.tag != "routes":
        raise ValueError(f"unexpected_route_root:{root.tag}")

    stage2_route_id = "stage23_trigger_merge_upbound"
    stage3_route_id = "stage23_trigger_caseb_m09"
    stage2_edges = route_slice(stage1.route_edges, stage1.departure.mainline_target_edge, 8)
    caseb_movement = next((movement for movement in stage1.movements if movement.movement_id == "B4_MOVEMENT_09"), None)
    if caseb_movement is None:
        raise ValueError("missing_B4_MOVEMENT_09")
    stage3_edges = route_slice(stage1.route_edges, caseb_movement.from_edge, 12)
    ensure_route(root, stage2_route_id, stage2_edges)
    ensure_route(root, stage3_route_id, stage3_edges)

    for idx in range(stage2_count):
        append_vehicle(
            root,
            vehicle_id=f"stage23_merge_trigger_{idx:03d}",
            route_id=stage2_route_id,
            depart=555.0 + idx * 0.25,
        )
    for idx in range(stage3_count):
        append_vehicle(
            root,
            vehicle_id=f"stage23_caseb_m09_trigger_{idx:03d}",
            route_id=stage3_route_id,
            depart=680.0 + idx * 1.0,
        )

    sort_vehicle_elements_by_depart(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output, encoding="utf-8", xml_declaration=True)
    summary = {
        "schema": "compact_v9_B04_stage23_trigger_demand.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_demand": rel(base_demand),
        "output_demand": rel(output),
        "stage2_route_id": stage2_route_id,
        "stage2_edges": stage2_edges,
        "stage2_vehicle_count": stage2_count,
        "stage2_depart_window_sec": [555.0, 555.0 + max(stage2_count - 1, 0) * 0.25],
        "stage3_route_id": stage3_route_id,
        "stage3_edges": stage3_edges,
        "stage3_vehicle_count": stage3_count,
        "stage3_depart_window_sec": [680.0, 680.0 + max(stage3_count - 1, 0) * 1.0],
        "notes": "Original demand is not modified; this is a selectable Stage2/Stage3 trigger demand variant.",
    }
    output.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Stage2/Stage3 trigger demand variant.")
    parser.add_argument("--base-demand", type=Path, default=DEFAULT_BASE_DEMAND)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--stage2-count", type=int, default=140)
    parser.add_argument("--stage3-count", type=int, default=36)
    args = parser.parse_args(argv)
    base_demand = args.base_demand if args.base_demand.is_absolute() else PROJECT_ROOT / args.base_demand
    output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    summary = build_stage23_trigger_demand(
        base_demand=base_demand,
        output=output,
        stage2_count=max(args.stage2_count, 0),
        stage3_count=max(args.stage3_count, 0),
        stage1=B4Stage1Inputs.load(),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
