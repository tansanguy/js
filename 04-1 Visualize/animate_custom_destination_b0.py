#!/usr/bin/env python3
"""Build B0-only animations for the accepted custom destinations."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

from config import HTML_OUTPUT_DIR, PROJECT_ROOT
from utils.animation_builder import build_animated_single_map_html
from utils.fcd_parser import FcdResult, parse_fcd


DEFAULT_LATEST_JSON = PROJECT_ROOT / "results/metrics/validated_custom_destination_b0/latest.json"
DEFAULT_ACCEPTED_ROUTES = PROJECT_ROOT / "data_prepared/validated/custom_routes/accepted_custom_routes.csv"
DEFAULT_BG_RADIUS_M = 250.0


class CustomB0AnimationError(RuntimeError):
    """Expected custom B0 animation failure."""


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def project_path(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else PROJECT_ROOT / value


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise CustomB0AnimationError(f"json_root_not_object:{rel(path)}")
    return payload


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def float_cell(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def bool_cell(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def route_slug(route_id: str) -> str:
    upper = route_id.upper()
    if "PILDONG" in upper:
        return "pildong"
    if "HOEHYEON" in upper:
        return "hoehyeon"
    slug = re.sub(r"[^a-z0-9]+", "_", route_id.lower()).strip("_")
    return slug or "custom_route"


def load_custom_b0_results(latest_json: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if not latest_json.is_file():
        raise CustomB0AnimationError(f"missing_latest_json:{rel(latest_json)}")
    latest = read_json(latest_json)
    results_csv = project_path(str(latest.get("results_csv", "")))
    if not results_csv.is_file():
        raise CustomB0AnimationError(f"missing_results_csv:{rel(results_csv)}")
    rows = [
        row
        for row in read_csv(results_csv)
        if row.get("mode") == "B0"
        and row.get("parameter_id") == "no_control"
        and row.get("route_id", "").startswith("CUSTOM_")
    ]
    if not rows:
        raise CustomB0AnimationError(f"no_custom_b0_rows:{rel(results_csv)}")
    rows.sort(key=lambda row: row.get("route_id", ""))
    return latest, rows


def load_accepted_routes(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        raise CustomB0AnimationError(f"missing_accepted_routes:{rel(path)}")
    rows = read_csv(path)
    required = {"route_id", "label_ko", "target_edge_id", "route_edges", "route_length_m", "lat", "lon"}
    missing = sorted(required - set(rows[0].keys())) if rows else sorted(required)
    if missing:
        raise CustomB0AnimationError(f"accepted_routes_missing_columns:{','.join(missing)}")
    routes = {row["route_id"]: row for row in rows if row.get("route_id")}
    if not routes:
        raise CustomB0AnimationError(f"accepted_routes_empty:{rel(path)}")
    return routes


def pair_results_with_routes(result_rows: list[dict[str, str]], routes: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    paired = []
    for row in result_rows:
        route_id = row["route_id"]
        route = routes.get(route_id)
        if route is None:
            raise CustomB0AnimationError(f"missing_accepted_route_for_result:{route_id}")
        run_dir = project_path(row.get("run_dir", ""))
        fcd = run_dir / "fcd.xml"
        if not fcd.is_file():
            raise CustomB0AnimationError(f"missing_fcd:{rel(fcd)}")
        paired.append({"result": row, "route": route, "run_dir": run_dir, "fcd": fcd})
    return paired


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * 111_320.0
    dlon = (lon2 - lon1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dlat, dlon)


def emergency_pos_by_time(fcd: FcdResult) -> dict[float, tuple[float, float]]:
    return {point.time: (point.lat, point.lon) for point in fcd.emergency.points}


def build_b0_payload(
    fcd: FcdResult,
    route_length_m: float,
    bg_radius_m: float,
    result_row: dict[str, str],
    route_row: dict[str, str],
) -> dict[str, Any]:
    emergency = fcd.emergency
    points = emergency.points
    if not points:
        raise CustomB0AnimationError(f"empty_emergency_trajectory:{fcd.emergency_id}")

    anchor = emergency.start_time
    cum = 0.0
    cumulative: list[float] = []
    previous = None
    for point in points:
        if previous is not None:
            cum += meters_between(previous.lat, previous.lon, point.lat, point.lon)
        cumulative.append(cum)
        previous = point
    raw_total = cumulative[-1] if cumulative else 0.0

    def normalized_distance(distance_m: float) -> float:
        return round(distance_m / raw_total * route_length_m, 2) if raw_total else 0.0

    series = [
        {
            "t_rel": round(point.time - anchor, 2),
            "lat": round(point.lat, 6),
            "lon": round(point.lon, 6),
            "speed_kmh": round(point.speed_kmh, 2),
            "angle": round(point.angle, 1),
            "dist_m": normalized_distance(cumulative[index]),
            "edge": point.edge_id,
        }
        for index, point in enumerate(points)
    ]

    emergency_positions = emergency_pos_by_time(fcd)
    background = []
    for snap in fcd.background:
        ref = emergency_positions.get(snap["time"])
        if ref is None:
            continue
        elat, elon = ref
        nearby = [
            {
                "id": vehicle.get("id", ""),
                "lat": round(vehicle["lat"], 6),
                "lon": round(vehicle["lon"], 6),
                "speed_kmh": float(vehicle["speed_kmh"]),
                "angle": float(vehicle["angle"]),
                "edge": vehicle.get("edge", ""),
                "lane": vehicle.get("lane", ""),
            }
            for vehicle in snap["vehicles"]
            if meters_between(elat, elon, vehicle["lat"], vehicle["lon"]) <= bg_radius_m
        ]
        if nearby:
            background.append({"t_rel": round(float(snap["time"]) - anchor, 2), "vehicles": nearby})

    travel_time = float_cell(result_row, "emergency_travel_time_sec", emergency.total_travel_time_sec)
    speeds = [point.speed_kmh for point in points]
    return {
        "mode": "B0",
        "route_id": result_row["route_id"],
        "destination_id": route_row.get("destination_id", ""),
        "label_ko": route_row.get("label_ko", result_row["route_id"]),
        "target_edge_id": route_row.get("target_edge_id", ""),
        "emergency_id": fcd.emergency_id,
        "travel_time_sec": round(travel_time, 2),
        "avg_speed_kmh": round(route_length_m / travel_time * 3.6, 2) if travel_time else 0.0,
        "max_speed_kmh": round(max(speeds), 2) if speeds else 0.0,
        "distance_m": round(route_length_m, 2),
        "depart_time_sec": anchor,
        "final_status": result_row.get("final_status", ""),
        "warning_reason": result_row.get("warning_reason", ""),
        "route_error_count": result_row.get("route_error_count", ""),
        "emergency_teleport": bool_cell(result_row.get("emergency_teleport")),
        "emergency_arrived": bool_cell(result_row.get("emergency_arrived")),
        "background_vehicle_count": result_row.get("background_vehicle_count", ""),
        "remaining_vehicle_count": result_row.get("remaining_vehicle_count", ""),
        "destination": {
            "lat": float_cell(route_row, "lat"),
            "lon": float_cell(route_row, "lon"),
            "address": route_row.get("address", ""),
        },
        "emergency": series,
        "background": background,
        "route_polyline": [[point["lat"], point["lon"]] for point in series],
    }


def bounds_for_doc(doc: dict[str, Any]) -> dict[str, float]:
    payload = doc["modes"]["B0"]
    points = list(payload["route_polyline"])
    destination = payload.get("destination", {})
    if destination.get("lat") and destination.get("lon"):
        points.append([destination["lat"], destination["lon"]])
    if not points:
        return {}
    lats = [float(point[0]) for point in points]
    lons = [float(point[1]) for point in points]
    return {
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
        "center_lat": (min(lats) + max(lats)) / 2.0,
        "center_lon": (min(lons) + max(lons)) / 2.0,
    }


def route_doc(pair: dict[str, Any], route_length_m: float, bg_radius_m: float) -> dict[str, Any]:
    fcd = parse_fcd(pair["fcd"], mode="B0")
    mode_payload = build_b0_payload(fcd, route_length_m, bg_radius_m, pair["result"], pair["route"])
    doc = {
        "schema": "custom_destination_b0_animation.v1",
        "meta": {
            "route_id": pair["result"]["route_id"],
            "route_length_m": route_length_m,
            "bg_radius_m": bg_radius_m,
            "run_dir": rel(pair["run_dir"]),
            "fcd_xml": rel(pair["fcd"]),
            "output_slug": route_slug(pair["result"]["route_id"]),
        },
        "modes": {"B0": mode_payload},
    }
    doc["meta"]["bounds"] = bounds_for_doc(doc)
    return doc


def write_index_html(route_outputs: list[dict[str, Any]], output_path: Path) -> None:
    cards = []
    for item in route_outputs:
        payload = item["doc"]["modes"]["B0"]
        href = Path(item["html"]).name
        cards.append(
            f"""<section class="card">
  <strong>{payload['label_ko']}</strong>
  <div class="muted"><code>{payload['route_id']}</code></div>
  <div class="metric"><span>target edge</span><code>{payload['target_edge_id']}</code></div>
  <div class="metric"><span>depart</span><b>{payload['depart_time_sec']:.0f}s</b></div>
  <div class="metric"><span>travel time</span><b>{payload['travel_time_sec']:.0f}s</b></div>
  <div class="metric"><span>background vehicles</span><b>{payload['background_vehicle_count']}</b></div>
  <div class="metric"><span>route error</span><b>{payload['route_error_count']}</b></div>
  <div class="metric"><span>emergency teleport</span><b>{str(payload['emergency_teleport']).lower()}</b></div>
  <a class="button" href="{href}">애니메이션 열기</a>
</section>"""
        )
    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Custom Destination B0 Animations</title>
<style>
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;color:#111827;}}
  main{{max-width:960px;margin:0 auto;padding:28px;}}
  h1{{font-size:24px;margin:0 0 18px;}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px;}}
  .card{{background:white;border:1px solid #d9e1e8;border-radius:8px;padding:16px;}}
  .metric{{display:grid;grid-template-columns:1fr auto;gap:8px;font-size:14px;padding:4px 0;border-bottom:1px solid #edf1f5;}}
  .metric:last-child{{border-bottom:0;}}
  a.button{{display:inline-block;margin-top:12px;background:#2563eb;color:white;text-decoration:none;padding:8px 12px;border-radius:6px;}}
  code{{font-size:12px;}}
  .muted{{color:#64748b;font-size:13px;}}
</style>
</head>
<body>
<main>
<h1>Custom Destination B0 Animations</h1>
<p class="muted">04_visualize clone renderer를 사용한 B0 no-control custom route 애니메이션입니다.</p>
<div class="grid">
{chr(10).join(cards)}
</div>
</main>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def build_outputs(
    latest_json: Path,
    accepted_routes: Path,
    bg_radius_m: float,
    output_dir: Path,
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    latest, result_rows = load_custom_b0_results(latest_json)
    routes = load_accepted_routes(accepted_routes)
    pairs = pair_results_with_routes(result_rows, routes)
    outputs = []
    for pair in pairs:
        route_length = float_cell(pair["route"], "route_length_m")
        if route_length <= 0:
            raise CustomB0AnimationError(f"invalid_route_length:{pair['result']['route_id']}")
        slug = route_slug(pair["result"]["route_id"])
        doc = route_doc(pair, route_length, bg_radius_m)
        json_path = output_dir / f"custom_destination_b0_animation_{slug}.json"
        html_path = output_dir / f"custom_destination_b0_animation_{slug}.html"
        json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        build_animated_single_map_html(doc, html_path, f"B0 Animation - {doc['modes']['B0']['label_ko']}")
        outputs.append({"route_id": pair["result"]["route_id"], "json": rel(json_path), "html": rel(html_path), "doc": doc})
    index_path = output_dir / "custom_destination_b0_animation_index.html"
    write_index_html(outputs, index_path)
    for item in outputs:
        item["index_html"] = rel(index_path)
        item["latest_json"] = rel(latest_json)
        item["run_id"] = latest.get("run_id", "")
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build B0-only custom destination animations.")
    parser.add_argument("--latest-json", type=Path, default=DEFAULT_LATEST_JSON)
    parser.add_argument("--accepted-routes", type=Path, default=DEFAULT_ACCEPTED_ROUTES)
    parser.add_argument("--bg-radius-m", type=float, default=DEFAULT_BG_RADIUS_M)
    parser.add_argument("--output-dir", type=Path, default=HTML_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        outputs = build_outputs(
            project_path(args.latest_json),
            project_path(args.accepted_routes),
            args.bg_radius_m,
            project_path(args.output_dir),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc}")
        return 1
    print(f"index: {outputs[0]['index_html'] if outputs else ''}")
    for item in outputs:
        payload = item["doc"]["modes"]["B0"]
        print(
            f"{item['route_id']}: html={item['html']} json={item['json']} "
            f"depart={payload['depart_time_sec']:.0f}s travel={payload['travel_time_sec']:.0f}s "
            f"bg_snaps={len(payload['background'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
