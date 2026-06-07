#!/usr/bin/env python3
"""Build an API-informed plausible signal network for Compact V9 B04/B4.

This is intentionally pragmatic.  The T-Data SPaT endpoint gives live
remaining-time fields but not a direct SUMO tls_id mapping, so this script uses
real API snapshots to derive plausible cycle/green/offset values, applies them
to every Toegye-ro mainline TLS, then infers the remaining TLS from the nearest
mainline profile.  The output is meant to make the B04/B4 signal network more
reasonable, not to claim exact field reproduction.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
import json
import math
import os
import ssl
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = Path(__file__).resolve().parent
TDATA_ROOT = PIPELINE_DIR / "tdata_signal"
RAW_DIR = TDATA_ROOT / "raw"
NET_DIR = TDATA_ROOT / "nets"
SUMMARY_DIR = TDATA_ROOT / "summaries"

BASE_NET = NET_DIR / "jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
LEGACY_GREEN18_NET = PROJECT_ROOT / "data_prepared/compact_v9/net/jungbu_compact_v9_B04_green18.net.xml"
MAINROAD_CSV = PROJECT_ROOT / "data_prepared/compact_v9/b4_stage1_s1forced/b4_intersections.csv"
OUTPUT_NET = NET_DIR / "jungbu_compact_v9_B04_tdata_plausible.net.xml"
ACTIVE_NET_BACKUP = NET_DIR / "jungbu_compact_v9_B04_global_reality_s1forced.before_tdata_plausible.net.xml"
MAINROAD_PROFILES_CSV = TDATA_ROOT / "mainroad_signal_profiles.csv"
INFERRED_PROFILES_CSV = TDATA_ROOT / "inferred_signal_profiles.csv"
GLOBAL_PROFILES_CSV = TDATA_ROOT / "global_signal_profiles.csv"
SUMMARY_JSON = SUMMARY_DIR / "tdata_plausible_signal_summary.json"
REMAINING_SUMMARY_JSON = SUMMARY_DIR / "tdata_spat_remaining_time_summary.json"
API_ENDPOINT = "https://t-data.seoul.go.kr/apig/apiman-gateway/tapi/v2xSignalPhaseTimingInformation/1.0"

MOVEMENT_SUFFIXES = ("Bssg", "Bcsg", "Ltsg", "Pdsg", "Stsg", "Utsg")
VEHICLE_MOVEMENT_SUFFIXES = ("Bssg", "Ltsg", "Stsg", "Utsg")
DIRECTION_PREFIXES = ("nt", "et", "st", "wt", "ne", "se", "sw", "nw")
REMAINING_FIELD_SUFFIX = "RmdrCs"
REMAINING_TIME_UNIT_SEC = 0.1
UNAVAILABLE_REMAINING_SENTINELS = {36000, 36001}
UNAVAILABLE_REMAINING_THRESHOLD_CS = 36000

MOVEMENT_LABELS_KO = {
    "Bssg": "버스",
    "Bcsg": "자전거",
    "Ltsg": "좌회전",
    "Pdsg": "보행",
    "Stsg": "직진",
    "Utsg": "유턴",
}


class TDataSignalError(RuntimeError):
    """Expected pipeline failure."""


@dataclass(frozen=True)
class ApiSignalRecord:
    itst_id: str
    eqmn_id: str
    data_id: str
    utc_ms: float
    reg_dt: str
    vehicle_values_sec: tuple[float, ...]
    dominant_field: str
    dominant_remaining_sec: float
    median_remaining_sec: float
    vehicle_field_count: float


@dataclass(frozen=True)
class SignalProfile:
    tls_id: str
    profile_role: str
    source: str
    source_itst_id: str
    source_eqmn_id: str
    source_tls_id: str
    movement_ids: str
    mapped_segments: str
    route_order_min: float
    cycle_sec: int
    main_green_sec: int
    side_green_sec: int
    yellow_sec: int
    offset_sec: int
    confidence: float
    dominant_api_field: str
    dominant_remaining_sec: float
    inference_reason: str

    def as_row(self) -> dict[str, Any]:
        return {
            "tls_id": self.tls_id,
            "profile_role": self.profile_role,
            "source": self.source,
            "source_itst_id": self.source_itst_id,
            "source_eqmn_id": self.source_eqmn_id,
            "source_tls_id": self.source_tls_id,
            "movement_ids": self.movement_ids,
            "mapped_segments": self.mapped_segments,
            "route_order_min": self.route_order_min,
            "cycle_sec": self.cycle_sec,
            "main_green_sec": self.main_green_sec,
            "side_green_sec": self.side_green_sec,
            "yellow_sec": self.yellow_sec,
            "offset_sec": self.offset_sec,
            "confidence": round(self.confidence, 3),
            "dominant_api_field": self.dominant_api_field,
            "dominant_remaining_sec": round(self.dominant_remaining_sec, 3),
            "inference_reason": self.inference_reason,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


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


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def round_to_5(value: float) -> int:
    return int(max(1, round(value / 5.0) * 5))


def clamp_int(value: float, low: int, high: int) -> int:
    return int(max(low, min(high, round(value))))


def signal_fields() -> tuple[str, ...]:
    return tuple(f"{prefix}{suffix}{REMAINING_FIELD_SUFFIX}" for prefix in DIRECTION_PREFIXES for suffix in VEHICLE_MOVEMENT_SUFFIXES)


def remaining_signal_fields(row: dict[str, Any]) -> list[str]:
    return [
        key for key in row
        if key.endswith(REMAINING_FIELD_SUFFIX)
        and len(key) > len(REMAINING_FIELD_SUFFIX) + 2
        and key[:2] in DIRECTION_PREFIXES
        and key[2:-len(REMAINING_FIELD_SUFFIX)] in MOVEMENT_SUFFIXES
    ]


def parse_remaining_field(field: str) -> tuple[str, str] | None:
    if not field.endswith(REMAINING_FIELD_SUFFIX) or len(field) <= len(REMAINING_FIELD_SUFFIX) + 2:
        return None
    direction = field[:2]
    movement = field[2:-len(REMAINING_FIELD_SUFFIX)]
    if direction not in DIRECTION_PREFIXES or movement not in MOVEMENT_SUFFIXES:
        return None
    return direction, movement


def remaining_cs_to_sec(value: Any) -> float | None:
    raw = safe_float(value, math.nan)
    if math.isnan(raw):
        return None
    if raw in UNAVAILABLE_REMAINING_SENTINELS or raw >= UNAVAILABLE_REMAINING_THRESHOLD_CS:
        return None
    if raw <= 0:
        return None
    return raw * REMAINING_TIME_UNIT_SEC


def valid_remaining_values(row: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for field in remaining_signal_fields(row):
        parsed = parse_remaining_field(field)
        remaining_sec = remaining_cs_to_sec(row.get(field))
        if parsed is None or remaining_sec is None:
            continue
        direction, movement = parsed
        values.append({
            "field": field,
            "direction": direction,
            "movement": movement,
            "movement_label_ko": MOVEMENT_LABELS_KO.get(movement, movement),
            "remaining_sec": remaining_sec,
        })
    return values


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * pct))))
    return ordered[index]


def remaining_time_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    all_values: list[float] = []
    by_movement: dict[str, list[float]] = defaultdict(list)
    snapshot_rows: list[dict[str, Any]] = []
    non_positive_count = 0
    sentinel_count = 0
    null_count = 0
    for record in records:
        snapshot_values: list[float] = []
        for field in remaining_signal_fields(record):
            raw = record.get(field)
            if raw in (None, ""):
                null_count += 1
                continue
            raw_float = safe_float(raw, math.nan)
            if math.isnan(raw_float):
                null_count += 1
                continue
            if raw_float in UNAVAILABLE_REMAINING_SENTINELS or raw_float >= UNAVAILABLE_REMAINING_THRESHOLD_CS:
                sentinel_count += 1
                continue
            if raw_float <= 0:
                non_positive_count += 1
                continue
            parsed = parse_remaining_field(field)
            if parsed is None:
                continue
            _direction, movement = parsed
            remaining_sec = raw_float * REMAINING_TIME_UNIT_SEC
            all_values.append(remaining_sec)
            by_movement[movement].append(remaining_sec)
            snapshot_values.append(remaining_sec)
        if snapshot_values:
            snapshot_rows.append({
                "itst_id": str(record.get("itstId", "")),
                "eqmn_id": str(record.get("eqmnId", "")),
                "valid_field_count": len(snapshot_values),
                "mean_remaining_sec": sum(snapshot_values) / len(snapshot_values),
            })
    movement_summary = {
        movement: {
            "label_ko": MOVEMENT_LABELS_KO.get(movement, movement),
            "count": len(values),
            "mean_sec": round(sum(values) / len(values), 3) if values else 0.0,
            "median_sec": round(median(values), 3) if values else 0.0,
            "min_sec": round(min(values), 3) if values else 0.0,
            "max_sec": round(max(values), 3) if values else 0.0,
        }
        for movement, values in sorted(by_movement.items())
    }
    snapshot_means = [row["mean_remaining_sec"] for row in snapshot_rows]
    snapshot_counts = [float(row["valid_field_count"]) for row in snapshot_rows]
    return {
        "schema": "seoul_spat_remaining_time_summary.v1",
        "record_count": len(records),
        "valid_remaining_count": len(all_values),
        "excluded_null_count": null_count,
        "excluded_non_positive_count": non_positive_count,
        "excluded_sentinel_count": sentinel_count,
        "remaining_time_unit_sec": REMAINING_TIME_UNIT_SEC,
        "overall": {
            "mean_sec": round(sum(all_values) / len(all_values), 3) if all_values else 0.0,
            "median_sec": round(median(all_values), 3) if all_values else 0.0,
            "min_sec": round(min(all_values), 3) if all_values else 0.0,
            "max_sec": round(max(all_values), 3) if all_values else 0.0,
        },
        "by_movement": movement_summary,
        "snapshot": {
            "snapshot_count": len(snapshot_rows),
            "mean_valid_field_count": round(sum(snapshot_counts) / len(snapshot_counts), 3) if snapshot_counts else 0.0,
            "mean_of_snapshot_mean_remaining_sec": round(sum(snapshot_means) / len(snapshot_means), 3) if snapshot_means else 0.0,
            "median_of_snapshot_mean_remaining_sec": round(median(snapshot_means), 3) if snapshot_means else 0.0,
        },
        "interpretation_ko": (
            "서울 SPaT API 값은 방향·이동류별 잔여시간이다. "
            "이 요약은 G/Y/R 평균이 아니라 현재 시점의 유효 잔여시간 통계다."
        ),
    }


def inferred_vehicle_service_duration_sec(values: list[float]) -> float:
    if not values:
        return 0.0
    # If samples are uniformly distributed within an active signal group, mean remaining is about half service duration.
    # The p90 cap prevents one stale high remaining value from dominating the inferred simulation profile.
    return min(2.0 * (sum(values) / len(values)), percentile(values, 0.9))


def inferred_service_by_movement(records: list[dict[str, Any]]) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for record in records:
        for item in valid_remaining_values(record):
            grouped[str(item["movement"])].append(float(item["remaining_sec"]))
    return {
        movement: round(inferred_vehicle_service_duration_sec(values), 3)
        for movement, values in grouped.items()
        if movement in VEHICLE_MOVEMENT_SUFFIXES and values
    }


def inferred_gyr_from_remaining_summary(summary: dict[str, Any], yellow_sec: float = 3.0) -> dict[str, Any]:
    overall_mean = safe_float(summary.get("overall", {}).get("mean_sec"))
    by_movement = summary.get("by_movement", {})
    straight_mean = safe_float(by_movement.get("Stsg", {}).get("mean_sec"), overall_mean)
    cycle = clamp_int(round_to_5(max(60.0, 2.0 * overall_mean)), 60, 140)
    green = clamp_int(round_to_5(straight_mean), 18, max(19, cycle - 2 * int(yellow_sec) - 12))
    red = max(6, cycle - green - int(yellow_sec))
    return {
        "schema": "seoul_spat_inferred_gyr_profile.v1",
        "cycle_sec": cycle,
        "green_sec": green,
        "yellow_sec": int(yellow_sec),
        "red_sec": red,
        "method": (
            "RmdrCs는 G/Y/R 직접값이 아니므로 전체 평균 잔여시간의 2배를 cycle proxy로 두고, "
            "직진(Stsg) 평균 잔여시간을 대표 green proxy로 사용한 뒤 red=cycle-green-yellow로 역산한다."
        ),
        "not_direct_api_fields": True,
    }


def fetch_page(api_key: str, *, page_no: int, num_rows: int, timeout_sec: int = 60) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({
        "apikey": api_key,
        "type": "json",
        "pageNo": page_no,
        "numOfRows": num_rows,
    })
    request = urllib.request.Request(f"{API_ENDPOINT}?{params}", headers={"User-Agent": "codex-tdata-sumo/1.0"})
    try:
        response_ctx = urllib.request.urlopen(request, timeout=timeout_sec)
    except urllib.error.URLError as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise
        response_ctx = urllib.request.urlopen(request, timeout=timeout_sec, context=ssl._create_unverified_context())
    with response_ctx as response:
        body = response.read().decode("utf-8")
    payload = json.loads(body)
    if not isinstance(payload, list):
        raise TDataSignalError(f"unexpected_api_payload:{type(payload).__name__}")
    return payload


def collect_api_samples(api_key: str, *, pages: int, num_rows: int, samples: int, interval_sec: float) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    output = RAW_DIR / f"tdata_spat_samples_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    with output.open("w", encoding="utf-8") as file:
        for sample_index in range(samples):
            for page_no in range(1, pages + 1):
                records = fetch_page(api_key, page_no=page_no, num_rows=num_rows)
                file.write(json.dumps({
                    "sample_index": sample_index,
                    "page_no": page_no,
                    "fetched_at": utc_now(),
                    "record_count": len(records),
                    "records": records,
                }, ensure_ascii=False) + "\n")
            if sample_index < samples - 1:
                time.sleep(interval_sec)
    return output


def load_sample_records(sample_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with sample_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            payload = json.loads(line)
            records.extend(payload.get("records", []))
    return records


def sample_stats(sample_path: Path) -> dict[str, Any]:
    line_count = 0
    page_numbers: set[int] = set()
    record_count = 0
    with sample_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            payload = json.loads(line)
            line_count += 1
            page_numbers.add(int(payload.get("page_no", 0) or 0))
            record_count += int(payload.get("record_count", len(payload.get("records", []))) or 0)
    return {
        "sample_line_count": line_count,
        "sample_page_count": len(page_numbers),
        "sample_pages": " ".join(str(item) for item in sorted(page_numbers)),
        "sample_record_count": record_count,
    }


def api_record_from_row(row: dict[str, Any]) -> ApiSignalRecord | None:
    values_by_field: dict[str, float] = {}
    for field in signal_fields():
        value = safe_float(row.get(field), math.nan)
        if value in UNAVAILABLE_REMAINING_SENTINELS or math.isnan(value) or value <= 0 or value >= UNAVAILABLE_REMAINING_THRESHOLD_CS:
            continue
        values_by_field[field] = value / 10.0
    if not values_by_field:
        return None
    values = tuple(sorted(values_by_field.values()))
    dominant_field, dominant_remaining = max(values_by_field.items(), key=lambda item: item[1])
    median = values[len(values) // 2]
    return ApiSignalRecord(
        itst_id=str(row.get("itstId", "")),
        eqmn_id=str(row.get("eqmnId", "")),
        data_id=str(row.get("dataId", "")),
        utc_ms=safe_float(row.get("trsmUtcTime"), 0.0),
        reg_dt=str(row.get("regDt", "")),
        vehicle_values_sec=values,
        dominant_field=dominant_field,
        dominant_remaining_sec=dominant_remaining,
        median_remaining_sec=median,
        vehicle_field_count=len(values),
    )


def _dedupe_api_records(records: list[ApiSignalRecord]) -> list[ApiSignalRecord]:
    deduped: dict[str, ApiSignalRecord] = {}
    for record in records:
        key = record.data_id.strip() if record.data_id.strip() else (
            f"{record.itst_id}:{record.utc_ms:.3f}:{record.dominant_field}:"
            f"{record.dominant_remaining_sec:.3f}:{record.median_remaining_sec:.3f}:{record.vehicle_field_count:.3f}"
        )
        previous = deduped.get(key)
        if previous is None or record.utc_ms >= previous.utc_ms:
            deduped[key] = record
    return list(deduped.values())


def aggregate_api_records_by_itst(records: list[dict[str, Any]]) -> dict[str, ApiSignalRecord]:
    parsed = [record for row in records if (record := api_record_from_row(row)) is not None and record.itst_id]
    deduped = _dedupe_api_records(parsed)
    grouped: dict[str, list[ApiSignalRecord]] = defaultdict(list)
    for record in deduped:
        grouped[record.itst_id].append(record)

    aggregated: dict[str, ApiSignalRecord] = {}
    for itst_id, items in grouped.items():
        items = sorted(items, key=lambda item: (item.utc_ms, item.data_id))
        latest = items[-1]
        dominant_counter = Counter(item.dominant_field for item in items if item.dominant_field)
        if dominant_counter:
            best_count = max(dominant_counter.values())
            dominant_field = sorted(field for field, count in dominant_counter.items() if count == best_count)[0]
        else:
            dominant_field = latest.dominant_field
        dominant_remaining_sec = sum(item.dominant_remaining_sec for item in items) / len(items)
        median_remaining_sec = sum(item.median_remaining_sec for item in items) / len(items)
        vehicle_field_count = sum(item.vehicle_field_count for item in items) / len(items)
        aggregated[itst_id] = ApiSignalRecord(
            itst_id=itst_id,
            eqmn_id=latest.eqmn_id,
            data_id=f"avg:{itst_id}:{len(items)}",
            utc_ms=latest.utc_ms,
            reg_dt=latest.reg_dt,
            vehicle_values_sec=tuple(sorted((dominant_remaining_sec, median_remaining_sec))),
            dominant_field=dominant_field,
            dominant_remaining_sec=dominant_remaining_sec,
            median_remaining_sec=median_remaining_sec,
            vehicle_field_count=vehicle_field_count,
        )
    return aggregated


def select_api_records(records: list[dict[str, Any]], required_count: int) -> list[ApiSignalRecord]:
    aggregated = aggregate_api_records_by_itst(records)
    ranked = sorted(
        aggregated.values(),
        key=lambda item: (
            item.vehicle_field_count,
            min(item.dominant_remaining_sec, 140.0),
            item.median_remaining_sec,
            item.itst_id,
        ),
        reverse=True,
    )
    if not ranked:
        raise TDataSignalError("no_valid_api_signal_records")
    while len(ranked) < required_count:
        ranked.extend(ranked[: required_count - len(ranked)])
    return ranked[:required_count]


def mainroad_rows() -> list[dict[str, str]]:
    rows = []
    for row in read_csv(MAINROAD_CSV):
        if row.get("tls_id"):
            rows.append(row)
    return rows


def profile_from_api(row: dict[str, str], api_record: ApiSignalRecord, index: int) -> SignalProfile:
    dominant = api_record.dominant_remaining_sec
    median = api_record.median_remaining_sec
    route_order = safe_float(row.get("route_order_min"), index)
    cycle = clamp_int(round_to_5(max(60.0, median * 2.0, dominant + 6.0)), 60, 140)
    yellow = 3
    main_green = clamp_int(round_to_5(median), 18, cycle - 2 * yellow - 12)
    side_green = max(6, cycle - main_green - 2 * yellow)
    offset = int((route_order * 4.7 + dominant) % cycle)
    confidence = min(0.9, 0.52 + api_record.vehicle_field_count * 0.035)
    return SignalProfile(
        tls_id=row["tls_id"],
        profile_role="mainroad_direct",
        source="TData_SPAT_sample_direct_order_mapping",
        source_itst_id=api_record.itst_id,
        source_eqmn_id=api_record.eqmn_id,
        source_tls_id=row["tls_id"],
        movement_ids=row.get("movement_ids", ""),
        mapped_segments=row.get("mapped_S_segments", ""),
        route_order_min=route_order,
        cycle_sec=cycle,
        main_green_sec=main_green,
        side_green_sec=side_green,
        yellow_sec=yellow,
        offset_sec=offset,
        confidence=confidence,
        dominant_api_field=api_record.dominant_field,
        dominant_remaining_sec=dominant,
        inference_reason="G/Y/R is inferred from RmdrCs remaining-time statistics; API does not directly provide color durations.",
    )


def global_profile_from_api(tls_id: str, api_record: ApiSignalRecord, index: int) -> SignalProfile:
    dominant = api_record.dominant_remaining_sec
    median = api_record.median_remaining_sec
    cycle = clamp_int(round_to_5(max(60.0, median * 2.0, dominant + 6.0)), 60, 140)
    yellow = 3
    main_green = clamp_int(round_to_5(median), 18, cycle - 2 * yellow - 12)
    side_green = max(5, cycle - main_green - 2 * yellow)
    offset = int((index * 5.3 + dominant) % cycle)
    confidence = min(0.82, 0.46 + api_record.vehicle_field_count * 0.03)
    return SignalProfile(
        tls_id=tls_id,
        profile_role="global_api_direct",
        source="TData_SPAT_sample_global_tls_order_mapping",
        source_itst_id=api_record.itst_id,
        source_eqmn_id=api_record.eqmn_id,
        source_tls_id=tls_id,
        movement_ids="",
        mapped_segments="",
        route_order_min=float(index),
        cycle_sec=cycle,
        main_green_sec=main_green,
        side_green_sec=side_green,
        yellow_sec=yellow,
        offset_sec=offset,
        confidence=confidence,
        dominant_api_field=api_record.dominant_field,
        dominant_remaining_sec=dominant,
        inference_reason="G/Y/R is inferred from RmdrCs remaining-time statistics; API does not directly provide color durations.",
    )


def infer_profile(tls_id: str, source: SignalProfile, distance_rank: int) -> SignalProfile:
    cycle = source.cycle_sec
    main_green = clamp_int(source.main_green_sec * 0.82, 18, cycle - 16)
    side_green = max(6, cycle - main_green - 2 * source.yellow_sec)
    offset = int((source.offset_sec + 7 * distance_rank) % cycle)
    return SignalProfile(
        tls_id=tls_id,
        profile_role="inferred_other",
        source="nearest_mainroad_api_profile",
        source_itst_id=source.source_itst_id,
        source_eqmn_id=source.source_eqmn_id,
        source_tls_id=source.tls_id,
        movement_ids="",
        mapped_segments="",
        route_order_min=source.route_order_min,
        cycle_sec=cycle,
        main_green_sec=main_green,
        side_green_sec=side_green,
        yellow_sec=source.yellow_sec,
        offset_sec=offset,
        confidence=max(0.25, source.confidence - 0.22),
        dominant_api_field=source.dominant_api_field,
        dominant_remaining_sec=source.dominant_remaining_sec,
        inference_reason="non-mainroad TLS inferred from nearest mainroad profile in net tlLogic order",
    )


def parse_index_list(value: str) -> list[int]:
    result = []
    for token in str(value or "").split():
        try:
            result.append(int(token))
        except ValueError:
            continue
    return result


def is_yellow_phase(phase: ET.Element) -> bool:
    state = phase.get("state", "")
    return "y" in state or "Y" in state


def auto_main_phase_indices(phases: list[ET.Element]) -> list[int]:
    best_index = 0
    best_score = -1
    for index, phase in enumerate(phases):
        state = phase.get("state", "")
        if is_yellow_phase(phase):
            continue
        score = state.count("G") * 2 + state.count("g")
        if score > best_score:
            best_index = index
            best_score = score
    return [best_index]


def distribute(total: int, count: int, minimum: int = 1) -> list[int]:
    if count <= 0:
        return []
    total = max(total, count * minimum)
    base = total // count
    remainder = total % count
    return [base + (1 if index < remainder else 0) for index in range(count)]


def apply_profile_to_logic(logic: ET.Element, profile: SignalProfile, main_indices: list[int] | None) -> dict[str, Any]:
    phases = list(logic.findall("phase"))
    if not phases:
        return {"status": "SKIP", "reason": "no_phase"}
    valid_main = [index for index in (main_indices or []) if 0 <= index < len(phases)]
    if not valid_main:
        valid_main = auto_main_phase_indices(phases)
    yellow_indices = [index for index, phase in enumerate(phases) if is_yellow_phase(phase)]
    non_yellow_non_main = [index for index in range(len(phases)) if index not in valid_main and index not in yellow_indices]

    before_cycle = sum(safe_float(phase.get("duration"), 0.0) for phase in phases)
    yellow_total = min(len(yellow_indices) * profile.yellow_sec, max(0, profile.cycle_sec - len(valid_main)))
    main_total = min(profile.main_green_sec, max(1, profile.cycle_sec - yellow_total - len(non_yellow_non_main)))
    side_total = max(0, profile.cycle_sec - yellow_total - main_total)

    new_durations = [1 for _ in phases]
    for index, duration in zip(yellow_indices, distribute(yellow_total, len(yellow_indices), minimum=1), strict=False):
        new_durations[index] = duration
    for index, duration in zip(valid_main, distribute(main_total, len(valid_main), minimum=1), strict=False):
        new_durations[index] = duration
    for index, duration in zip(non_yellow_non_main, distribute(side_total, len(non_yellow_non_main), minimum=1), strict=False):
        new_durations[index] = duration

    delta = profile.cycle_sec - sum(new_durations)
    adjust_candidates = valid_main or non_yellow_non_main or list(range(len(phases)))
    new_durations[adjust_candidates[-1]] += delta
    for phase, duration in zip(phases, new_durations, strict=True):
        phase.set("duration", str(max(1, int(duration))))
    logic.set("type", "static")
    logic.set("offset", str(profile.offset_sec))
    if not logic.get("programID"):
        logic.set("programID", "TDATA_PLAUSIBLE")
    return {
        "status": "APPLIED",
        "before_cycle_sec": round(before_cycle, 3),
        "after_cycle_sec": sum(new_durations),
        "phase_count": len(phases),
        "main_phase_indices": " ".join(str(index) for index in valid_main),
        "yellow_phase_count": len(yellow_indices),
    }


def build_profiles(sample_path: Path) -> tuple[list[SignalProfile], list[SignalProfile]]:
    main_rows = mainroad_rows()
    api_records = select_api_records(load_sample_records(sample_path), len(main_rows))
    main_profiles = [profile_from_api(row, api_records[index], index) for index, row in enumerate(main_rows)]
    return main_profiles, []


def nearest_main_profile(tls_index: int, main_positions: dict[str, int], main_profiles: list[SignalProfile]) -> tuple[SignalProfile, int]:
    best_profile = main_profiles[0]
    best_distance = 10**9
    for profile in main_profiles:
        distance = abs(tls_index - main_positions.get(profile.tls_id, tls_index))
        if distance < best_distance:
            best_profile = profile
            best_distance = distance
    return best_profile, best_distance


def apply_profiles_to_net(
    *,
    input_net: Path,
    output_net: Path,
    main_profiles: list[SignalProfile],
    global_profiles: list[SignalProfile],
    overwrite_active_net: bool,
) -> dict[str, Any]:
    tree = ET.parse(input_net)
    root = tree.getroot()
    logics = [logic for logic in root.findall("tlLogic") if logic.get("id")]
    logic_by_id = {str(logic.get("id")): logic for logic in logics}
    main_by_id = {profile.tls_id: profile for profile in main_profiles}
    global_by_id = {profile.tls_id: profile for profile in global_profiles}
    main_rows_by_tls = {row["tls_id"]: row for row in mainroad_rows()}
    main_positions = {str(logic.get("id")): index for index, logic in enumerate(logics) if str(logic.get("id")) in main_by_id}

    applied_rows: list[dict[str, Any]] = []
    inferred_profiles: list[SignalProfile] = []
    for index, logic in enumerate(logics):
        tls_id = str(logic.get("id"))
        if tls_id in main_by_id:
            profile = main_by_id[tls_id]
            main_indices = parse_index_list(main_rows_by_tls.get(tls_id, {}).get("selected_green_phases", ""))
        elif tls_id in global_by_id:
            profile = global_by_id[tls_id]
            main_indices = None
        else:
            source, distance = nearest_main_profile(index, main_positions, main_profiles)
            profile = infer_profile(tls_id, source, distance)
            inferred_profiles.append(profile)
            main_indices = None
        result = apply_profile_to_logic(logic, profile, main_indices)
        applied_rows.append(profile.as_row() | result)

    output_net.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_net, encoding="UTF-8", xml_declaration=True)
    active_overwritten = False
    if overwrite_active_net:
        raise TDataSignalError(
            "overwrite_active_net_disabled: B04/B4 active net is fixed to the canonical S1-forced global-reality net; "
            "write a separate --output-net and promote it through b04_global_reality_signal_pipeline.py."
        )

    write_csv(MAINROAD_PROFILES_CSV, [profile.as_row() for profile in main_profiles], list(main_profiles[0].as_row().keys()))
    if inferred_profiles:
        write_csv(INFERRED_PROFILES_CSV, [profile.as_row() for profile in inferred_profiles], list(inferred_profiles[0].as_row().keys()))
    if global_profiles:
        write_csv(GLOBAL_PROFILES_CSV, [profile.as_row() for profile in global_profiles], list(global_profiles[0].as_row().keys()))
    write_csv(TDATA_ROOT / "applied_signal_profiles.csv", applied_rows, list(applied_rows[0].keys()))
    return {
        "input_net": rel(input_net),
        "output_net": rel(output_net),
        "active_net": rel(BASE_NET),
        "legacy_green18_net": rel(LEGACY_GREEN18_NET),
        "active_net_overwritten": active_overwritten,
        "active_backup": rel(ACTIVE_NET_BACKUP) if ACTIVE_NET_BACKUP.is_file() else "",
        "tl_logic_count": len(logics),
        "mainroad_profile_count": len(main_profiles),
        "global_api_direct_profile_count": len(global_profiles),
        "inferred_profile_count": len(inferred_profiles),
        "applied_profile_csv": rel(TDATA_ROOT / "applied_signal_profiles.csv"),
        "mainroad_profiles_csv": rel(MAINROAD_PROFILES_CSV),
        "global_profiles_csv": rel(GLOBAL_PROFILES_CSV) if global_profiles else "",
        "inferred_profiles_csv": rel(INFERRED_PROFILES_CSV) if inferred_profiles else "",
    }


def tl_logic_ids(net_file: Path) -> list[str]:
    root = ET.parse(net_file).getroot()
    return [str(logic.get("id")) for logic in root.findall("tlLogic") if logic.get("id")]


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    api_key = args.api_key or os.environ.get("SEOUL_TDATA_API_KEY", "")
    sample_path: Path
    if args.sample_path:
        sample_path = args.sample_path
    else:
        if not api_key:
            raise TDataSignalError("missing_api_key: set SEOUL_TDATA_API_KEY or pass --api-key")
        sample_path = collect_api_samples(
            api_key,
            pages=args.pages,
            num_rows=args.num_rows,
            samples=args.samples,
            interval_sec=args.interval_sec,
        )
    records = load_sample_records(sample_path)
    if args.remaining_summary_only:
        summary = remaining_time_summary(records)
        summary["inferred_gyr_profile"] = inferred_gyr_from_remaining_summary(summary)
        write_json(REMAINING_SUMMARY_JSON, summary)
        return summary
    main_rows = mainroad_rows()
    main_api_records = select_api_records(records, len(main_rows))
    main_profiles = [profile_from_api(row, main_api_records[index], index) for index, row in enumerate(main_rows)]
    global_profiles: list[SignalProfile] = []
    if args.global_api_direct:
        logic_ids = tl_logic_ids(args.input_net)
        main_ids = {profile.tls_id for profile in main_profiles}
        api_records = select_api_records(records, len(logic_ids))
        global_profiles = [
            global_profile_from_api(tls_id, api_records[index], index)
            for index, tls_id in enumerate(logic_ids)
            if tls_id not in main_ids
        ]
    apply_summary = apply_profiles_to_net(
        input_net=args.input_net,
        output_net=args.output_net,
        main_profiles=main_profiles,
        global_profiles=global_profiles,
        overwrite_active_net=args.overwrite_active_net,
    )
    stats = sample_stats(sample_path)
    summary = {
        "schema": "compact_v9_tdata_plausible_signal.v1",
        "generated_at": utc_now(),
        "claim_scope": "API-informed plausible signal network, not exact field signal reproduction",
        "api_endpoint": API_ENDPOINT,
        "sample_path": rel(sample_path),
        "samples": args.samples if not args.sample_path else "loaded_existing_sample",
        "pages": args.pages if not args.sample_path else stats["sample_page_count"],
        "sample_pages": stats["sample_pages"],
        "sample_record_count": stats["sample_record_count"],
        "num_rows": args.num_rows,
        "global_api_direct": args.global_api_direct,
        **apply_summary,
    }
    write_json(SUMMARY_JSON, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect T-Data SPaT samples and build a plausible B04/B4 signal net.")
    parser.add_argument("--api-key", default=None, help="T-Data API key. Prefer SEOUL_TDATA_API_KEY.")
    parser.add_argument("--sample-path", type=Path, default=None, help="Existing raw JSONL sample to reuse.")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--num-rows", type=int, default=100)
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--interval-sec", type=float, default=2.0)
    parser.add_argument("--input-net", type=Path, default=BASE_NET)
    parser.add_argument("--output-net", type=Path, default=OUTPUT_NET)
    parser.add_argument("--overwrite-active-net", action="store_true", help="Disabled guard: active B04/B4 uses the canonical S1-forced net.")
    parser.add_argument("--global-api-direct", action="store_true", help="Assign API-derived profiles to all non-mainroad TLS instead of inferred profiles.")
    parser.add_argument("--remaining-summary-only", action="store_true", help="Only summarize direction/movement remaining times; does not build a SUMO signal net.")
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
