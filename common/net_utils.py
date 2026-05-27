"""Network build helpers for OSM and SUMO preparation steps."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from common.geo_utils import geojson_feature


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"ERROR: JSON root must be an object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def validate_bbox_wgs84(bbox: dict[str, Any]) -> dict[str, float]:
    required = ["min_lon", "min_lat", "max_lon", "max_lat"]
    for key in required:
        if key not in bbox:
            raise ValueError(f"ERROR: missing bbox_wgs84 field: {key}")
        if not isinstance(bbox[key], int | float):
            raise ValueError(f"ERROR: bbox_wgs84.{key} must be numeric")

    clean = {key: float(bbox[key]) for key in required}
    if not -180 <= clean["min_lon"] <= 180 or not -180 <= clean["max_lon"] <= 180:
        raise ValueError("ERROR: invalid bbox_wgs84 longitude range")
    if not -90 <= clean["min_lat"] <= 90 or not -90 <= clean["max_lat"] <= 90:
        raise ValueError("ERROR: invalid bbox_wgs84 latitude range")
    if clean["min_lon"] >= clean["max_lon"]:
        raise ValueError("ERROR: invalid bbox_wgs84: min_lon must be less than max_lon")
    if clean["min_lat"] >= clean["max_lat"]:
        raise ValueError("ERROR: invalid bbox_wgs84: min_lat must be less than max_lat")
    return clean


def build_overpass_query(bbox: dict[str, float]) -> str:
    south = bbox["min_lat"]
    west = bbox["min_lon"]
    north = bbox["max_lat"]
    east = bbox["max_lon"]
    return f"""
[out:xml][timeout:180];
(
  way["highway"]({south},{west},{north},{east});
);
(._;>;);
out meta;
""".strip()


def download_osm_bbox(
    bbox: dict[str, float],
    output_path: Path,
    overpass_url: str,
    timeout_sec: int,
    verify_ssl: bool = True,
) -> None:
    query = build_overpass_query(bbox)
    encoded = urllib.parse.urlencode({"data": query}).encode("utf-8")
    request = urllib.request.Request(
        overpass_url,
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    context = None
    if overpass_url.startswith("https://") and not verify_ssl:
        context = ssl._create_unverified_context()  # noqa: SLF001 - explicit fallback for local cert-store issues.

    try:
        with urllib.request.urlopen(request, timeout=timeout_sec, context=context) as response:
            tmp_path.write_bytes(response.read())
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        raise RuntimeError(f"ERROR: failed to download OSM data from Overpass API: {exc}") from exc

    tmp_path.replace(output_path)


def validate_osm_xml(path: Path) -> dict[str, int]:
    if not path.is_file():
        raise ValueError(f"ERROR: invalid OSM file: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"ERROR: OSM file is empty: {path}")

    counts = {"node_count": 0, "way_count": 0, "relation_count": 0}
    try:
        for _event, elem in ET.iterparse(path, events=("end",)):
            if elem.tag == "node":
                counts["node_count"] += 1
            elif elem.tag == "way":
                counts["way_count"] += 1
            elif elem.tag == "relation":
                counts["relation_count"] += 1
            elem.clear()
    except ET.ParseError as exc:
        raise ValueError(f"ERROR: invalid OSM file: {path}") from exc

    if counts["way_count"] == 0:
        raise ValueError(f"ERROR: invalid OSM file: no highway ways found in {path}")
    return counts


def find_executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(f"ERROR: {name} executable not found in PATH")
    return path


def run_netconvert(command: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    sumo_home = env.get("SUMO_HOME")
    if sumo_home:
        current_home = Path(sumo_home)
        if not (current_home / "data/typemap/osmNetconvert.typ.xml").is_file():
            framework_home = current_home / "share/sumo"
            if (framework_home / "data/typemap/osmNetconvert.typ.xml").is_file():
                env["SUMO_HOME"] = str(framework_home)
    else:
        executable = shutil.which("netconvert")
        if executable:
            candidate_home = Path(executable).resolve().parents[1] / "share/sumo"
            if (candidate_home / "data/typemap/osmNetconvert.typ.xml").is_file():
                env["SUMO_HOME"] = str(candidate_home)

    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


def validate_sumo_net_xml(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"ERROR: net.xml was not created: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"ERROR: net.xml is empty: {path}")

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"ERROR: invalid net.xml: {path}") from exc
    if root.tag != "net":
        raise ValueError(f"ERROR: invalid net.xml root: expected net, got {root.tag}")


def count_net_elements(path: Path) -> dict[str, int]:
    counts = {
        "edge_count": 0,
        "junction_count": 0,
        "traffic_light_count": 0,
        "lane_count": 0,
    }
    tls_ids: set[str] = set()

    for _event, elem in ET.iterparse(path, events=("end",)):
        if elem.tag == "edge" and elem.get("function") != "internal":
            counts["edge_count"] += 1
        elif elem.tag == "junction":
            counts["junction_count"] += 1
            if elem.get("type") == "traffic_light":
                tls_id = elem.get("id")
                if tls_id:
                    tls_ids.add(tls_id)
        elif elem.tag == "tlLogic":
            tls_id = elem.get("id")
            if tls_id:
                tls_ids.add(tls_id)
        elif elem.tag == "lane":
            counts["lane_count"] += 1
        elem.clear()

    counts["traffic_light_count"] = len(tls_ids)
    return counts


def sumo_version() -> str:
    executable = shutil.which("sumo")
    if executable is None:
        return "sumo not found"
    completed = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    output = (completed.stdout or completed.stderr).strip().splitlines()
    return output[0] if output else "unknown"


def read_sumo_net(net_file: Path) -> Any:
    try:
        from sumolib import net
    except ImportError as exc:
        raise ImportError("ERROR: Python import sumolib failed; run Step 0 environment check") from exc

    try:
        return net.readNet(str(net_file))
    except Exception as exc:  # noqa: BLE001 - sumolib exposes mixed parser errors.
        raise RuntimeError("ERROR: failed to read SUMO net.xml with sumolib") from exc


def convert_shape_xy_to_lonlat(sumo_net: Any, shape: list[tuple[float, float]]) -> list[list[float]]:
    coordinates: list[list[float]] = []
    for point in shape:
        if len(point) < 2:
            raise ValueError("shape point does not contain x/y coordinates")
        lon, lat = sumo_net.convertXY2LonLat(float(point[0]), float(point[1]))
        coordinates.append([float(lon), float(lat)])
    return coordinates


def edge_allows_vehicle_class(edge: Any, vehicle_class: str) -> bool:
    try:
        return bool(edge.allows(vehicle_class))
    except Exception:  # noqa: BLE001 - keep export running; caller records conservative result.
        return False


def edge_shape_with_lane_fallback(edge: Any) -> list[tuple[float, float]]:
    shape = edge.getShape()
    if shape:
        return shape

    lanes = edge.getLanes()
    for lane in lanes:
        lane_shape = lane.getShape()
        if lane_shape:
            return lane_shape
    return []


def extract_edge_feature(sumo_net: Any, edge: Any) -> dict[str, Any]:
    edge_id = edge.getID()
    shape = edge_shape_with_lane_fallback(edge)
    if not shape:
        raise ValueError(f"WARNING: edge has no usable shape and was skipped: {edge_id}")

    coordinates = convert_shape_xy_to_lonlat(sumo_net, shape)
    if len(coordinates) < 2:
        raise ValueError(f"WARNING: edge has too few shape points and was skipped: {edge_id}")

    edge_function = edge.getFunction() or ""
    is_internal = edge_function == "internal" or edge_id.startswith(":")
    allows_passenger = edge_allows_vehicle_class(edge, "passenger")
    allows_emergency = edge_allows_vehicle_class(edge, "emergency")

    return geojson_feature(
        "LineString",
        coordinates,
        {
            "edge_id": edge_id,
            "from_node": edge.getFromNode().getID() if edge.getFromNode() is not None else None,
            "to_node": edge.getToNode().getID() if edge.getToNode() is not None else None,
            "length_m": float(edge.getLength()),
            "speed_mps": float(edge.getSpeed()),
            "lane_count": int(edge.getLaneNumber()),
            "priority": int(edge.getPriority()),
            "is_internal": is_internal,
            "edge_function": edge_function,
            "allows_passenger": allows_passenger,
            "allows_emergency_candidate": allows_emergency or allows_passenger,
            "shape_point_count": len(coordinates),
        },
    )


def parse_tllogic_counts(net_xml_path: Path) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for _event, elem in ET.iterparse(net_xml_path, events=("end",)):
        if elem.tag == "tlLogic":
            tls_id = elem.get("id")
            if tls_id:
                entry = counts.setdefault(tls_id, {"program_count": 0, "phase_count": 0})
                entry["program_count"] += 1
                entry["phase_count"] += sum(1 for child in elem if child.tag == "phase")
        elem.clear()
    return counts


def parse_tls_connection_counts(net_xml_path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _event, elem in ET.iterparse(net_xml_path, events=("end",)):
        if elem.tag == "connection":
            tls_id = elem.get("tl")
            if tls_id:
                counts[tls_id] = counts.get(tls_id, 0) + 1
        elem.clear()
    return counts


def parse_traffic_light_junction_ids(net_xml_path: Path) -> set[str]:
    ids: set[str] = set()
    for _event, elem in ET.iterparse(net_xml_path, events=("end",)):
        if elem.tag == "junction" and elem.get("type") == "traffic_light" and elem.get("id"):
            ids.add(str(elem.get("id")))
        elem.clear()
    return ids


def inside_bbox(lon: float, lat: float, bbox_wgs84: dict[str, float]) -> bool:
    return (
        bbox_wgs84["min_lon"] <= lon <= bbox_wgs84["max_lon"]
        and bbox_wgs84["min_lat"] <= lat <= bbox_wgs84["max_lat"]
    )


def lane_position_from_tls(tls: Any) -> tuple[float, float] | None:
    points: list[tuple[float, float]] = []
    for connection in tls.getConnections():
        if not connection:
            continue
        lane = connection[0]
        shape = lane.getShape()
        if shape:
            points.append(shape[-1])
    if not points:
        return None
    avg_x = sum(point[0] for point in points) / len(points)
    avg_y = sum(point[1] for point in points) / len(points)
    return avg_x, avg_y


def extract_tls_features(
    sumo_net: Any,
    net_xml_path: Path,
    bbox_wgs84: dict[str, float],
) -> tuple[list[dict[str, Any]], list[str], int]:
    warnings: list[str] = []
    features: list[dict[str, Any]] = []
    tllogic_counts = parse_tllogic_counts(net_xml_path)
    connection_counts = parse_tls_connection_counts(net_xml_path)
    junction_ids = parse_traffic_light_junction_ids(net_xml_path)
    tls_by_id = {tls.getID(): tls for tls in sumo_net.getTrafficLights()}
    all_tls_ids = sorted(set(tllogic_counts) | set(connection_counts) | junction_ids | set(tls_by_id))

    for tls_id in all_tls_ids:
        node_id: str | None = None
        junction_id: str | None = None
        position_xy: tuple[float, float] | None = None

        try:
            node = sumo_net.getNode(tls_id)
            position_xy = node.getCoord()
            node_id = node.getID()
            junction_id = node.getID()
        except Exception:  # noqa: BLE001 - fallback to controlled-lane centroid.
            tls = tls_by_id.get(tls_id)
            if tls is not None:
                position_xy = lane_position_from_tls(tls)
                junction_id = tls_id

        if position_xy is None:
            warnings.append(f"WARNING: failed to resolve TLS position and skipped: {tls_id}")
            continue

        try:
            lon, lat = sumo_net.convertXY2LonLat(float(position_xy[0]), float(position_xy[1]))
        except Exception:  # noqa: BLE001 - record and continue.
            warnings.append(f"WARNING: failed to convert TLS coordinates and skipped: {tls_id}")
            continue

        counts = tllogic_counts.get(tls_id, {"program_count": 0, "phase_count": 0})
        feature = geojson_feature(
            "Point",
            [float(lon), float(lat)],
            {
                "tls_id": tls_id,
                "node_id": node_id,
                "junction_id": junction_id,
                "lon": float(lon),
                "lat": float(lat),
                "controlled_link_count": int(connection_counts.get(tls_id, 0)),
                "program_count": int(counts["program_count"]),
                "phase_count": int(counts["phase_count"]),
                "inside_analysis_bbox": inside_bbox(float(lon), float(lat), bbox_wgs84),
            },
        )
        features.append(feature)

    return features, warnings, len(all_tls_ids) - len(features)


def write_geojson(path: Path, features: list[dict[str, Any]]) -> None:
    payload = {
        "type": "FeatureCollection",
        "features": features,
    }
    write_json(path, payload)


def validate_feature_collection(path: Path) -> dict[str, int]:
    payload = load_json(path)
    if payload.get("type") != "FeatureCollection":
        raise ValueError(f"ERROR: invalid GeoJSON FeatureCollection: {path}")
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError(f"ERROR: GeoJSON features must be a list: {path}")
    return {"feature_count": len(features)}


def summarize_warnings(warnings: list[str]) -> dict[str, Any]:
    return {
        "warning_count": len(warnings),
        "sample": warnings[:50],
    }
