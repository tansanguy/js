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
