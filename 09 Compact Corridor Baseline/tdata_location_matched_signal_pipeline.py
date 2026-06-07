#!/usr/bin/env python3
"""Build a location-matched T-Data signal network for Compact V9 B04/B4.

This pipeline uses A008_P.csv as the Seoul intersection master.  It maps the
Toegye-ro mainstream skeleton endpoints to A008 intersections by coordinate,
uses the A008 intersection number as the T-Data API itstId candidate, filters
batch API snapshots locally, and applies matched signal profiles to B04 TLS.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import importlib.util
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pyproj import Transformer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = Path(__file__).resolve().parent
TDATA_ROOT = PIPELINE_DIR / "tdata_signal"
SNAPSHOT_DIR = TDATA_ROOT / "api_snapshots"
NET_DIR = TDATA_ROOT / "nets"
SUMMARY_DIR = TDATA_ROOT / "summaries"

A008_CSV = PROJECT_ROOT / "A008_P.csv"
SKELETON_CSV = PROJECT_ROOT / "mainstream_segment_skeleton.csv"
CANDIDATES_CSV = PROJECT_ROOT / "data_prepared/compact_v9/net/B04_csv_signal_candidates.csv"
INTERSECTIONS_CSV = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1_s1forced/b4_intersections.csv"
ACTIVE_NET = NET_DIR / "jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
LEGACY_GREEN18_NET = PROJECT_ROOT / "data_prepared/compact_v9/net/jungbu_compact_v9_B04_green18.net.xml"
LOCATION_BACKUP = NET_DIR / "jungbu_compact_v9_B04_global_reality_s1forced.before_location_matched.net.xml"
OUTPUT_NET = NET_DIR / "jungbu_compact_v9_B04_location_matched_s1forced.net.xml"

TIMING_ENDPOINT = "https://t-data.seoul.go.kr/apig/apiman-gateway/tapi/v2xSignalPhaseTimingInformation/1.0"
STATE_ENDPOINT = "https://t-data.seoul.go.kr/apig/apiman-gateway/tapi/v2xSignalPhaseInformation/1.0"

MAPPING_CSV = TDATA_ROOT / "a008_mainstream_itst_mapping.csv"
ENRICHED_SKELETON_CSV = TDATA_ROOT / "mainstream_segment_skeleton_a008_itst.csv"
TLS_MAPPING_CSV = TDATA_ROOT / "a008_tls_itst_mapping.csv"
PROFILES_CSV = TDATA_ROOT / "location_matched_signal_profiles.csv"
TRACI_METADATA_JSON = TDATA_ROOT / "location_matched_traci_signal_metadata.json"
APPLIED_CSV = TDATA_ROOT / "location_matched_applied_signal_profiles.csv"
SUMMARY_JSON = SUMMARY_DIR / "tdata_location_matched_signal_summary.json"
VALIDATION_MD = TDATA_ROOT / "LOCATION_MATCHED_VALIDATION_SUMMARY_KO.md"

TDATA_HELPER_PATH = PIPELINE_DIR / "tdata_plausible_signal_pipeline.py"


class LocationMatchedSignalError(RuntimeError):
    """Expected location-matched signal pipeline failure."""


@dataclass(frozen=True)
class A008Point:
    itst_id: str
    name: str
    normalized_id: str
    history_id: str
    x: float
    y: float
    lat: float
    lon: float


@dataclass(frozen=True)
class Endpoint:
    endpoint_id: str
    endpoint_role: str
    segment_id: str
    intersection_name: str
    lat: float
    lon: float


def load_tdata_helper() -> Any:
    spec = importlib.util.spec_from_file_location("tdata_plausible_signal_pipeline_helper", TDATA_HELPER_PATH)
    if spec is None or spec.loader is None:
        raise LocationMatchedSignalError(f"cannot_load_helper:{TDATA_HELPER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tdp = load_tdata_helper()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
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


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_name(value: str) -> str:
    value = re.sub(r"\([^)]*\)", "", str(value))
    value = value.replace("★", "")
    value = value.replace("사거리", "")
    value = value.replace("앞", "")
    value = value.replace("역", "")
    value = value.replace("교차로", "")
    value = value.replace(" ", "")
    return value.strip().lower()


def name_similarity(a: str, b: str) -> float:
    na = normalize_name(a)
    nb = normalize_name(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return math.hypot((lat1 - lat2) * 111000.0, (lon1 - lon2) * 88000.0)


def load_a008_points(path: Path = A008_CSV) -> list[A008Point]:
    transformer = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)
    points: list[A008Point] = []
    for row in read_csv(path):
        x = safe_float(row.get("XCE"), math.nan)
        y = safe_float(row.get("YCE"), math.nan)
        if math.isnan(x) or math.isnan(y):
            continue
        lon, lat = transformer.transform(x, y)
        points.append(A008Point(
            itst_id=str(row.get("교차로번호", "")).strip(),
            name=str(row.get("교차로명", "")).strip(),
            normalized_id=str(row.get("신규정규화ID", "")).strip(),
            history_id=str(row.get("이력ID", "")).strip(),
            x=x,
            y=y,
            lat=lat,
            lon=lon,
        ))
    return points


def skeleton_rows(path: Path = SKELETON_CSV) -> list[dict[str, str]]:
    return read_csv(path)


def skeleton_endpoints(rows: list[dict[str, str]]) -> list[Endpoint]:
    seen: set[tuple[str, str, str]] = set()
    endpoints: list[Endpoint] = []
    for row in rows:
        segment_id = row["segment_id"]
        for role in ("start", "end"):
            key = (str(row.get(f"{role}_intersection", "")), str(row.get(f"{role}_lat", "")), str(row.get(f"{role}_lon", "")))
            if key in seen:
                continue
            seen.add(key)
            endpoints.append(Endpoint(
                endpoint_id=f"{segment_id}:{role}",
                endpoint_role=role,
                segment_id=segment_id,
                intersection_name=str(row.get(f"{role}_intersection", "")).strip(),
                lat=safe_float(row.get(f"{role}_lat")),
                lon=safe_float(row.get(f"{role}_lon")),
            ))
    return endpoints


def endpoint_match_row(endpoint: Endpoint, points: list[A008Point]) -> dict[str, Any]:
    ranked = sorted(
        (
            (distance_m(endpoint.lat, endpoint.lon, point.lat, point.lon), name_similarity(endpoint.intersection_name, point.name), point)
            for point in points
        ),
        key=lambda item: (item[0], -item[1]),
    )[:3]
    best_distance, best_name_score, best = ranked[0]
    if best_distance <= 60.0:
        status = "auto_confirmed_distance"
    elif best_distance <= 150.0 and best_name_score >= 0.35:
        status = "auto_confirmed_distance_name"
    else:
        status = "manual_review"
    alternatives = [
        f"{point.itst_id}:{point.name}:{dist:.1f}m:name={score:.2f}"
        for dist, score, point in ranked[1:]
    ]
    confidence = max(0.0, min(1.0, (1.0 - best_distance / 180.0) * 0.7 + best_name_score * 0.3))
    return {
        "endpoint_id": endpoint.endpoint_id,
        "segment_id": endpoint.segment_id,
        "endpoint_role": endpoint.endpoint_role,
        "skeleton_intersection": endpoint.intersection_name,
        "skeleton_lat": endpoint.lat,
        "skeleton_lon": endpoint.lon,
        "itst_id": best.itst_id,
        "a008_name": best.name,
        "a008_lat": best.lat,
        "a008_lon": best.lon,
        "a008_xce": best.x,
        "a008_yce": best.y,
        "a008_normalized_id": best.normalized_id,
        "a008_history_id": best.history_id,
        "distance_m": round(best_distance, 3),
        "name_similarity": round(best_name_score, 3),
        "match_status": status,
        "match_confidence": round(confidence, 3),
        "alternatives": " | ".join(alternatives),
    }


def build_endpoint_mapping() -> list[dict[str, Any]]:
    points = load_a008_points()
    endpoints = skeleton_endpoints(skeleton_rows())
    rows = [endpoint_match_row(endpoint, points) for endpoint in endpoints]
    fields = [
        "endpoint_id", "segment_id", "endpoint_role", "skeleton_intersection", "skeleton_lat", "skeleton_lon",
        "itst_id", "a008_name", "a008_lat", "a008_lon", "a008_xce", "a008_yce",
        "a008_normalized_id", "a008_history_id", "distance_m", "name_similarity",
        "match_status", "match_confidence", "alternatives",
    ]
    write_csv(MAPPING_CSV, rows, fields)
    return rows


def write_enriched_skeleton(mapping_rows: list[dict[str, Any]]) -> None:
    by_endpoint = {row["endpoint_id"]: row for row in mapping_rows}
    rows: list[dict[str, Any]] = []
    for row in skeleton_rows():
        enriched: dict[str, Any] = dict(row)
        for role in ("start", "end"):
            match = by_endpoint.get(f"{row['segment_id']}:{role}", {})
            enriched[f"{role}_itst_id"] = match.get("itst_id", "")
            enriched[f"{role}_a008_name"] = match.get("a008_name", "")
            enriched[f"{role}_a008_distance_m"] = match.get("distance_m", "")
            enriched[f"{role}_a008_match_status"] = match.get("match_status", "")
        rows.append(enriched)
    fields = list(rows[0].keys()) if rows else []
    write_csv(ENRICHED_SKELETON_CSV, rows, fields)


def endpoint_name_is_virtual(name: str) -> bool:
    return "차로변화" in str(name)


def choose_candidate_endpoint(candidate: dict[str, str], endpoint_map: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    explicit = str(candidate.get("endpoint_source", "")).strip()
    if ":" in explicit:
        segment_id, role = explicit.split(":", 1)
        row = endpoint_map.get((segment_id, role))
        if row:
            score = 0.0
            score += 2.0 if row.get("match_status", "").startswith("auto_confirmed") else 0.0
            score += 1.0 if not endpoint_name_is_virtual(str(row.get("skeleton_intersection", ""))) else 0.0
            score += safe_float(row.get("match_confidence"), 0.0)
            score -= safe_float(row.get("distance_m"), 999.0) / 1000.0
            return dict(row) | {
                "candidate_endpoint_score": round(score, 6),
                "candidate_endpoint_source": explicit,
            }
    choices: list[dict[str, Any]] = []
    from_segment = candidate.get("from_segment", "")
    to_segment = candidate.get("to_segment", "")
    for segment_id, role in ((from_segment, "end"), (to_segment, "start")):
        row = endpoint_map.get((segment_id, role))
        if row:
            score = 0.0
            score += 2.0 if row.get("match_status", "").startswith("auto_confirmed") else 0.0
            score += 1.0 if not endpoint_name_is_virtual(str(row.get("skeleton_intersection", ""))) else 0.0
            score += safe_float(row.get("match_confidence"), 0.0)
            score -= safe_float(row.get("distance_m"), 999.0) / 1000.0
            choices.append(dict(row) | {"candidate_endpoint_score": round(score, 6), "candidate_endpoint_source": f"{segment_id}:{role}"})
    if not choices:
        return {}
    return sorted(choices, key=lambda item: item["candidate_endpoint_score"], reverse=True)[0]


def build_tls_mapping(mapping_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    endpoint_map = {(str(row["segment_id"]), str(row["endpoint_role"])): row for row in mapping_rows}
    intersection_rows = {row["tls_id"]: row for row in read_csv(INTERSECTIONS_CSV)}
    rows: list[dict[str, Any]] = []
    for candidate in read_csv(CANDIDATES_CSV):
        tls_id = candidate.get("tls_id", "")
        if not tls_id:
            continue
        chosen = choose_candidate_endpoint(candidate, endpoint_map)
        stage1 = intersection_rows.get(tls_id, {})
        rows.append({
            "tls_id": tls_id,
            "boundary_id": candidate.get("boundary_id", ""),
            "candidate_intersection_name": candidate.get("intersection_name", ""),
            "from_segment": candidate.get("from_segment", ""),
            "to_segment": candidate.get("to_segment", ""),
            "route_pair_index": candidate.get("route_pair_index", ""),
            "route_from_edge": candidate.get("route_from_edge", ""),
            "route_to_edge": candidate.get("route_to_edge", ""),
            "selected_green_phases": stage1.get("selected_green_phases", ""),
            "selected_red_phases": stage1.get("selected_red_phases", ""),
            "movement_ids": stage1.get("movement_ids", ""),
            "mapped_S_segments": stage1.get("mapped_S_segments", ""),
            "itst_id": chosen.get("itst_id", ""),
            "a008_name": chosen.get("a008_name", ""),
            "skeleton_intersection": chosen.get("skeleton_intersection", ""),
            "endpoint_source": chosen.get("candidate_endpoint_source", ""),
            "distance_m": chosen.get("distance_m", ""),
            "name_similarity": chosen.get("name_similarity", ""),
            "match_status": chosen.get("match_status", "missing_endpoint"),
            "match_confidence": chosen.get("match_confidence", ""),
            "candidate_endpoint_score": chosen.get("candidate_endpoint_score", ""),
        })
    fields = list(rows[0].keys()) if rows else []
    write_csv(TLS_MAPPING_CSV, rows, fields)
    return rows


def request_page(endpoint: str, api_key: str, page_no: int, num_rows: int, timeout_sec: int = 20) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({
        "apikey": api_key,
        "type": "json",
        "pageNo": page_no,
        "numOfRows": num_rows,
    })
    request = urllib.request.Request(f"{endpoint}?{params}", headers={"User-Agent": "codex-tdata-location-matched/1.0"})
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec, context=ssl._create_unverified_context()) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise LocationMatchedSignalError(f"unexpected_api_payload:{type(payload).__name__}")
            return payload
        except Exception as exc:
            last_error = exc
            time.sleep(1.0)
    assert last_error is not None
    raise last_error


def collect_snapshot(api_key: str, endpoint: str, label: str, start_page: int, max_pages: int, num_rows: int) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    output = SNAPSHOT_DIR / f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    with output.open("w", encoding="utf-8") as file:
        empty_pages = 0
        error_pages = 0
        end_page = start_page + max_pages - 1
        for page_no in range(start_page, end_page + 1):
            error = ""
            try:
                records = request_page(endpoint, api_key, page_no, num_rows)
            except Exception as exc:
                records = []
                error = f"{type(exc).__name__}:{str(exc)[:180]}"
                error_pages += 1
            else:
                error_pages = 0
            if not records and not error:
                empty_pages += 1
            else:
                empty_pages = 0
            file.write(json.dumps({
                "endpoint": endpoint,
                "label": label,
                "page_no": page_no,
                "fetched_at": utc_now(),
                "record_count": len(records),
                "error": error,
                "records": records,
            }, ensure_ascii=False) + "\n")
            file.flush()
            if empty_pages >= 2:
                break
            if error_pages >= 5:
                break
    return output


def load_snapshot(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            payload = json.loads(line)
            records.extend(payload.get("records", []))
    return records


def snapshot_stats(path: Path) -> dict[str, Any]:
    lines = 0
    records = 0
    pages: list[int] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            payload = json.loads(line)
            lines += 1
            records += int(payload.get("record_count", 0) or 0)
            pages.append(int(payload.get("page_no", 0) or 0))
    return {
        "path": rel(path),
        "line_count": lines,
        "record_count": records,
        "first_page": min(pages) if pages else "",
        "last_page": max(pages) if pages else "",
    }


def load_snapshots(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        records.extend(load_snapshot(path))
    return records


def archived_timing_snapshots() -> list[Path]:
    return sorted(path for path in SNAPSHOT_DIR.glob("timing_location_matched_*.jsonl") if path.is_file())


def snapshot_stats_many(paths: list[Path]) -> dict[str, Any]:
    stats = [snapshot_stats(path) for path in paths if path and path.is_file()]
    return {
        "files": stats,
        "file_count": len(stats),
        "record_count": sum(int(item.get("record_count", 0) or 0) for item in stats),
        "first_page": min((int(item["first_page"]) for item in stats if item.get("first_page") != ""), default=""),
        "last_page": max((int(item["last_page"]) for item in stats if item.get("last_page") != ""), default=""),
    }


def averaged_timing_by_itst(records: list[dict[str, Any]]) -> dict[str, Any]:
    return tdp.aggregate_api_records_by_itst(records)


def latest_state_by_itst(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in records:
        itst_id = str(row.get("itstId", ""))
        if not itst_id:
            continue
        previous = latest.get(itst_id)
        if previous is None or safe_float(row.get("trsmUtcTime"), 0.0) >= safe_float(previous.get("trsmUtcTime"), 0.0):
            latest[itst_id] = row
    return latest


def round_to_5(value: float) -> int:
    return int(max(1, round(value / 5.0) * 5))


def clamp_int(value: float, low: int, high: int) -> int:
    return int(max(low, min(high, round(value))))


def measured_profile_average(profiles: list[Any]) -> dict[str, int]:
    measured = [profile for profile in profiles if "TData_SPAT" in str(profile.source)]
    if not measured:
        return {"cycle_sec": 90, "main_green_sec": 60, "yellow_sec": 3}
    return {
        "cycle_sec": round(sum(int(profile.cycle_sec) for profile in measured) / len(measured)),
        "main_green_sec": round(sum(int(profile.main_green_sec) for profile in measured) / len(measured)),
        "yellow_sec": round(sum(int(profile.yellow_sec) for profile in measured) / len(measured)),
    }


def fallback_timing_family(row: dict[str, Any], index: int, averages: dict[str, int]) -> dict[str, int]:
    route_order = safe_float(row.get("route_pair_index"), index)
    yellow = int(averages["yellow_sec"])
    family_index = int(route_order + index) % 5
    cycle = clamp_int(int(averages["cycle_sec"]) + [-5, 0, 5, 10, -10][family_index], 80, 105)
    main_green = clamp_int(
        int(averages["main_green_sec"]) + [-12, -7, -2, 3, -5][family_index],
        28,
        cycle - 2 * yellow - 14,
    )
    return {
        "cycle_sec": cycle,
        "main_green_sec": main_green,
        "yellow_sec": yellow,
    }


def profile_from_match(
    row: dict[str, Any],
    timing_record: Any | None,
    state_record: dict[str, Any] | None,
    index: int,
    fallback_average: dict[str, int] | None = None,
) -> Any:
    route_order = safe_float(row.get("route_pair_index"), index)
    if timing_record is None:
        timing = fallback_timing_family(row, index, fallback_average or {"cycle_sec": 90, "main_green_sec": 60, "yellow_sec": 3})
        cycle = timing["cycle_sec"]
        yellow = timing["yellow_sec"]
        main_green = timing["main_green_sec"]
        dominant = 0.0
        median = 0.0
        source = "A008_location_matched_TData_measured_average_pm3_fallback"
        api_field = ""
        eqmn_id = ""
        confidence = 0.5
        timing_count = 0
    else:
        dominant = timing_record.dominant_remaining_sec
        median = timing_record.median_remaining_sec
        cycle = clamp_int(round_to_5(max(60.0, median * 2.0, dominant + 6.0)), 60, 140)
        main_green = clamp_int(round_to_5(median), 18, cycle - 2 * 3 - 12)
        source = "A008_location_matched_TData_SPAT"
        api_field = timing_record.dominant_field
        eqmn_id = timing_record.eqmn_id
        confidence = min(0.92, safe_float(row.get("match_confidence"), 0.5) * 0.55 + timing_record.vehicle_field_count * 0.045)
        timing_count = timing_record.vehicle_field_count
        yellow = 3
    side_green = max(10, cycle - main_green - 2 * yellow)
    offset = int((route_order * 4.8 + dominant * 0.25) % cycle)
    state_fields = [
        key for key, value in (state_record or {}).items()
        if key.endswith("StatNm") and value not in (None, "")
    ]
    reason = (
        f"location matched by A008 distance/name; timing_fields={timing_count}; "
        f"state_fields={len(state_fields)}; match_status={row.get('match_status', '')}"
    )
    if timing_record is not None:
        reason += "; G/Y/R inferred from RmdrCs remaining-time statistics, not direct API color durations"
    if timing_record is None:
        reason += "; fallback_policy=measured_average_route_family"
    return tdp.SignalProfile(
        tls_id=str(row.get("tls_id", "")),
        profile_role="location_matched_mainroad",
        source=source,
        source_itst_id=str(row.get("itst_id", "")),
        source_eqmn_id=eqmn_id,
        source_tls_id=str(row.get("tls_id", "")),
        movement_ids=str(row.get("movement_ids", "")),
        mapped_segments=str(row.get("mapped_S_segments", "")),
        route_order_min=route_order,
        cycle_sec=cycle,
        main_green_sec=main_green,
        side_green_sec=side_green,
        yellow_sec=yellow,
        offset_sec=offset,
        confidence=confidence,
        dominant_api_field=api_field,
        dominant_remaining_sec=dominant,
        inference_reason=reason,
    )


def apply_location_profiles(input_net: Path, output_net: Path, profiles: list[Any], overwrite_active_net: bool) -> dict[str, Any]:
    tree = ET.parse(input_net)
    root = tree.getroot()
    profile_by_tls = {profile.tls_id: profile for profile in profiles}
    tls_rows = {row["tls_id"]: row for row in read_csv(TLS_MAPPING_CSV)}
    applied_rows: list[dict[str, Any]] = []
    applied_count = 0
    for logic in root.findall("tlLogic"):
        tls_id = str(logic.get("id", ""))
        profile = profile_by_tls.get(tls_id)
        if profile is None:
            continue
        main_indices = tdp.parse_index_list(tls_rows.get(tls_id, {}).get("selected_green_phases", ""))
        result = tdp.apply_profile_to_logic(logic, profile, main_indices)
        applied_rows.append(profile.as_row() | result)
        if result.get("status") == "APPLIED":
            applied_count += 1
    output_net.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_net, encoding="UTF-8", xml_declaration=True)
    active_overwritten = False
    if overwrite_active_net:
        raise LocationMatchedSignalError(
            "overwrite_active_net_disabled: B04/B4 active net is fixed to the canonical S1-forced global-reality net; "
            "write a separate --output-net and promote it through b04_global_reality_signal_pipeline.py."
        )
    write_csv(APPLIED_CSV, applied_rows, list(applied_rows[0].keys()) if applied_rows else [])
    return {
        "input_net": rel(input_net),
        "output_net": rel(output_net),
        "active_net": rel(ACTIVE_NET),
        "legacy_green18_net": rel(LEGACY_GREEN18_NET),
        "active_net_overwritten": active_overwritten,
        "active_backup": rel(LOCATION_BACKUP) if LOCATION_BACKUP.is_file() else "",
        "applied_tls_count": applied_count,
        "applied_profiles_csv": rel(APPLIED_CSV),
    }


def write_traci_metadata(profiles: list[Any], tls_rows: list[dict[str, Any]]) -> None:
    by_tls = {row["tls_id"]: row for row in tls_rows}
    payload = {
        "schema": "compact_v9_location_matched_traci_signal_metadata.v1",
        "generated_at": utc_now(),
        "profile_source": rel(PROFILES_CSV),
        "items": [],
    }
    for profile in profiles:
        row = by_tls.get(profile.tls_id, {})
        payload["items"].append({
            "tls_id": profile.tls_id,
            "itst_id": profile.source_itst_id,
            "a008_name": row.get("a008_name", ""),
            "movement_ids": row.get("movement_ids", ""),
            "mapped_segments": row.get("mapped_S_segments", ""),
            "route_pair_index": row.get("route_pair_index", ""),
            "selected_green_phases": row.get("selected_green_phases", ""),
            "selected_red_phases": row.get("selected_red_phases", ""),
            "cycle_sec": profile.cycle_sec,
            "main_green_sec": profile.main_green_sec,
            "side_green_sec": profile.side_green_sec,
            "offset_sec": profile.offset_sec,
            "source": profile.source,
        })
    write_json(TRACI_METADATA_JSON, payload)


def load_smoke_result_rows(results_csv: Path | None, run_id: str | None) -> list[dict[str, str]]:
    if not results_csv or not results_csv.is_file():
        return []
    wanted_modes = {"B004": 0, "B04": 1, "B4": 2}
    filtered = [
        row for row in read_csv(results_csv)
        if (not run_id or row.get("run_id") == run_id) and row.get("mode") in wanted_modes
    ]
    latest_by_mode: dict[str, dict[str, str]] = {}
    for row in filtered:
        latest_by_mode[row.get("mode", "")] = row
    return [latest_by_mode[mode] for mode in sorted(latest_by_mode, key=wanted_modes.get)]


def write_validation_doc(summary: dict[str, Any], b04_b4_rows: list[dict[str, str]] | None = None) -> None:
    b04_b4_rows = b04_b4_rows or []
    result_lines = []
    for row in b04_b4_rows:
        result_lines.append(
            f"| {row.get('mode', '')} | {row.get('final_status', '')} | {row.get('termination_reason', '')} | "
            f"{row.get('T_actual_EMV_sec', '')} | {row.get('emergency_arrived', '')} | "
            f"{row.get('emergency_teleport', '')} | {row.get('background_arrived_ratio', '')} |"
        )
    if not result_lines:
        result_lines.append("| - | - | - | - | - | - | - |")
    VALIDATION_MD.write_text(
        "\n".join([
            "# A008 위치 매칭 T-Data 신호망 검증 요약",
            "",
            f"작성 시각: {utc_now()}",
            "",
            "## 구현 범위",
            "",
            "A008_P.csv의 교차로번호를 T-Data API itstId 후보로 사용하고, EPSG:5186 좌표를 WGS84로 변환해 mainstream skeleton endpoint와 거리 기반으로 매칭했다.",
            "개별 itstId API 호출은 쓰지 않고 batch snapshot을 수집한 뒤 로컬에서 matched itstId를 필터링했다.",
            "",
            "## 주요 산출물",
            "",
            f"- endpoint 매핑: `{rel(MAPPING_CSV)}`",
            f"- TLS 매핑: `{rel(TLS_MAPPING_CSV)}`",
            f"- 신호 프로파일: `{rel(PROFILES_CSV)}`",
            f"- TraCI 메타데이터: `{rel(TRACI_METADATA_JSON)}`",
            f"- 생성 net: `{summary.get('output_net', '')}`",
            "",
            "## 매칭/API 요약",
            "",
            f"- skeleton endpoint 수: {summary.get('endpoint_count', '')}",
            f"- endpoint 자동 확정 수: {summary.get('endpoint_auto_confirmed_count', '')}",
            f"- TLS 위치 매칭 수: {summary.get('tls_mapping_count', '')}",
            f"- T-Data timing hit 수: {summary.get('timing_hit_count', '')}",
            f"- T-Data state hit 수: {summary.get('state_hit_count', '')}",
            "",
            "## B04/B4 결과",
            "",
            "| mode | status | termination | EV time | EV arrived | teleport | background ratio |",
            "| --- | --- | --- | ---: | --- | --- | ---: |",
            *result_lines,
            "",
            "## 해석",
            "",
            "이 신호망은 현장 완전 재현이 아니라 실제 위치 itstId 기반 plausible 신호망이다. API snapshot에 없는 A008 교차로는 fallback 프로파일로 표시된다.",
        ]) + "\n",
        encoding="utf-8",
    )


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    mapping_rows = build_endpoint_mapping()
    write_enriched_skeleton(mapping_rows)
    tls_rows = build_tls_mapping(mapping_rows)

    api_key = args.api_key or os.environ.get("SEOUL_TDATA_API_KEY", "")
    if args.timing_snapshot:
        timing_snapshots = list(args.timing_snapshot)
    else:
        if not api_key:
            raise LocationMatchedSignalError("missing_api_key: set SEOUL_TDATA_API_KEY or pass --api-key")
        timing_snapshots = [collect_snapshot(api_key, TIMING_ENDPOINT, "timing_location_matched", args.start_page, args.max_pages, args.num_rows)]
        for path in archived_timing_snapshots():
            if path not in timing_snapshots:
                timing_snapshots.append(path)
    timing_snapshots.extend(args.fallback_timing_snapshot or [])
    if args.state_snapshot:
        state_snapshots = args.state_snapshot
    elif args.skip_state_snapshot:
        state_snapshots = []
    else:
        if not api_key:
            raise LocationMatchedSignalError("missing_api_key: set SEOUL_TDATA_API_KEY or pass --api-key")
        state_snapshots = [collect_snapshot(api_key, STATE_ENDPOINT, "state_location_matched", args.start_page, args.max_pages, args.num_rows)]

    timing_records = averaged_timing_by_itst(load_snapshots(timing_snapshots))
    state_records = latest_state_by_itst(load_snapshots(state_snapshots)) if state_snapshots else {}

    direct_profiles = [
        profile_from_match(row, timing_records[str(row.get("itst_id", ""))], state_records.get(str(row.get("itst_id", ""))), index)
        for index, row in enumerate(tls_rows)
        if str(row.get("itst_id", "")) in timing_records
    ]
    fallback_average = measured_profile_average(direct_profiles)
    profiles = [
        profile_from_match(
            row,
            timing_records.get(str(row.get("itst_id", ""))),
            state_records.get(str(row.get("itst_id", ""))),
            index,
            fallback_average,
        )
        for index, row in enumerate(tls_rows)
    ]
    write_csv(PROFILES_CSV, [profile.as_row() for profile in profiles], list(profiles[0].as_row().keys()) if profiles else [])
    write_traci_metadata(profiles, tls_rows)

    input_net = args.input_net
    if str(input_net) == "auto":
        input_net = ACTIVE_NET
    apply_summary = apply_location_profiles(Path(input_net), args.output_net, profiles, args.overwrite_active_net)

    endpoint_auto = sum(1 for row in mapping_rows if str(row.get("match_status", "")).startswith("auto_confirmed"))
    matched_itst_ids = {str(row.get("itst_id", "")) for row in tls_rows if row.get("itst_id")}
    timing_hits = matched_itst_ids & set(timing_records)
    state_hits = matched_itst_ids & set(state_records)
    summary = {
        "schema": "compact_v9_a008_location_matched_signal.v1",
        "generated_at": utc_now(),
        "claim_scope": "A008 location-matched T-Data plausible signal network, not exact field reproduction",
        "a008_csv": rel(A008_CSV),
        "skeleton_csv": rel(SKELETON_CSV),
        "mapping_csv": rel(MAPPING_CSV),
        "enriched_skeleton_csv": rel(ENRICHED_SKELETON_CSV),
        "tls_mapping_csv": rel(TLS_MAPPING_CSV),
        "profiles_csv": rel(PROFILES_CSV),
        "traci_metadata_json": rel(TRACI_METADATA_JSON),
        "timing_snapshot": snapshot_stats_many(timing_snapshots),
        "state_snapshot": snapshot_stats_many(state_snapshots) if state_snapshots else {},
        "endpoint_count": len(mapping_rows),
        "endpoint_auto_confirmed_count": endpoint_auto,
        "endpoint_manual_review_count": len(mapping_rows) - endpoint_auto,
        "tls_mapping_count": len(tls_rows),
        "matched_unique_itst_count": len(matched_itst_ids),
        "timing_hit_count": len(timing_hits),
        "timing_missing_itst_ids": sorted(matched_itst_ids - set(timing_records)),
        "fallback_average_policy": "measured_TData_profiles_average_with_route_family_timing",
        "fallback_average": fallback_average,
        "state_hit_count": len(state_hits),
        "state_missing_itst_ids": sorted(matched_itst_ids - set(state_records)) if state_records else [],
        **apply_summary,
    }
    write_json(SUMMARY_JSON, summary)
    write_validation_doc(summary, load_smoke_result_rows(args.results_csv, args.results_run_id))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build A008 location-matched T-Data signal network for B04/B4.")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--timing-snapshot", type=Path, action="append", default=None)
    parser.add_argument("--fallback-timing-snapshot", type=Path, action="append", default=None)
    parser.add_argument("--state-snapshot", type=Path, action="append", default=None)
    parser.add_argument("--skip-state-snapshot", action="store_true")
    parser.add_argument("--max-pages", type=int, default=120)
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--num-rows", type=int, default=100)
    parser.add_argument("--input-net", default="auto", help="Input net path or 'auto' for the canonical B04/B4 S1-forced net.")
    parser.add_argument("--output-net", type=Path, default=OUTPUT_NET)
    parser.add_argument("--overwrite-active-net", action="store_true", help="Disabled guard: active B04/B4 uses the canonical S1-forced net.")
    parser.add_argument("--results-csv", type=Path, default=None, help="Optional experiment_results.csv to include B04/B4 rows in the validation document.")
    parser.add_argument("--results-run-id", default=None, help="Optional run_id filter for --results-csv.")
    args = parser.parse_args(argv)
    try:
        summary = run_pipeline(args)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
