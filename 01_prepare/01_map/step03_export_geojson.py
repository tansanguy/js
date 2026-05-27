#!/usr/bin/env python3
"""Export SUMO net.xml edge and traffic-light layers to GeoJSON."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.net_utils import (  # noqa: E402
    extract_edge_feature,
    extract_tls_features,
    load_json,
    read_sumo_net,
    summarize_warnings,
    validate_bbox_wgs84,
    validate_feature_collection,
    write_geojson,
    write_json,
)


CONFIG_PATH = PROJECT_ROOT / "config/map_config.yaml"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step03_export_geojson.log"


class Step03Error(RuntimeError):
    """Expected Step 3 failure with user-facing error text."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_lines(lines: list[str]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def require_field(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for key in dotted_path.split("."):
        if not isinstance(current, dict) or key not in current:
            raise Step03Error(f"ERROR: missing required config field: {dotted_path}")
        current = current[key]
    return current


def load_yaml_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        raise Step03Error(f"ERROR: config file not found: {CONFIG_PATH.relative_to(PROJECT_ROOT)}")
    try:
        import yaml
    except ImportError as exc:
        raise Step03Error(
            "ERROR: PyYAML is required to read config/map_config.yaml. "
            "Install it with: python3 -m pip install PyYAML"
        ) from exc

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise Step03Error("ERROR: config/map_config.yaml must contain a YAML mapping")
    return config


def rel_path(path_text: str) -> Path:
    return PROJECT_ROOT / path_text


def load_required_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise Step03Error(f"ERROR: {label} not found: {path.relative_to(PROJECT_ROOT)}")
    return load_json(path)


def export_edges(sumo_net: Any) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
    features: list[dict[str, Any]] = []
    warnings: list[str] = []
    stats = {
        "skipped_edge_count": 0,
        "internal_edge_count": 0,
        "passenger_allowed_edge_count": 0,
        "emergency_candidate_edge_count": 0,
    }

    for edge in sumo_net.getEdges(withInternal=True):
        try:
            feature = extract_edge_feature(sumo_net, edge)
        except ValueError as exc:
            warnings.append(str(exc))
            stats["skipped_edge_count"] += 1
            continue
        except Exception:  # noqa: BLE001 - keep export running and audit failed edge.
            warnings.append(f"WARNING: failed to convert edge shape coordinates: {edge.getID()}")
            stats["skipped_edge_count"] += 1
            continue

        props = feature["properties"]
        if props["is_internal"]:
            stats["internal_edge_count"] += 1
        if props["allows_passenger"]:
            stats["passenger_allowed_edge_count"] += 1
        if props["allows_emergency_candidate"]:
            stats["emergency_candidate_edge_count"] += 1
        features.append(feature)

    return features, warnings, stats


def main() -> int:
    lines = [
        "Step 3 SUMO net GeoJSON export",
        "==============================",
        f"Project root: {PROJECT_ROOT}",
        "Policy: export edge/TLS GeoJSON only; no netconvert, OSM download, or HTML generation.",
    ]

    try:
        config = load_yaml_config()
        net_file = rel_path(require_field(config, "outputs.net_file"))
        analysis_area_geojson = rel_path(require_field(config, "outputs.analysis_area_geojson"))
        analysis_area_meta = rel_path(require_field(config, "outputs.analysis_area_meta"))
        net_audit = rel_path(require_field(config, "outputs.net_audit"))
        edges_geojson = rel_path(require_field(config, "outputs.sumo_edges_geojson"))
        tls_geojson = rel_path(require_field(config, "outputs.sumo_tls_geojson"))
        audit_output = rel_path(require_field(config, "outputs.step03_geojson_audit"))

        if not net_file.is_file():
            raise Step03Error(f"ERROR: net.xml not found: {net_file.relative_to(PROJECT_ROOT)}")
        if not analysis_area_geojson.is_file():
            raise Step03Error(
                f"ERROR: analysis area GeoJSON not found: {analysis_area_geojson.relative_to(PROJECT_ROOT)}"
            )

        meta = load_required_json(analysis_area_meta, "analysis area meta")
        if "bbox_wgs84" not in meta:
            raise Step03Error("ERROR: missing required meta field: bbox_wgs84")
        bbox_wgs84 = validate_bbox_wgs84(meta["bbox_wgs84"])

        step2_audit = load_required_json(net_audit, "Step 2 net audit")
        step2_tls_count = int(step2_audit.get("traffic_light_count", 0))

        sumo_net = read_sumo_net(net_file)
        edge_features, edge_warnings, edge_stats = export_edges(sumo_net)
        tls_features, tls_warnings, skipped_tls_count = extract_tls_features(
            sumo_net=sumo_net,
            net_xml_path=net_file,
            bbox_wgs84=bbox_wgs84,
        )

        if not edge_features:
            raise Step03Error("ERROR: edge Feature count is 0")
        if not tls_features:
            raise Step03Error("ERROR: TLS Feature count is 0")

        write_geojson(edges_geojson, edge_features)
        write_geojson(tls_geojson, tls_features)
        validate_feature_collection(edges_geojson)
        validate_feature_collection(tls_geojson)

        warnings = edge_warnings + tls_warnings
        audit = {
            "generated_at": utc_now(),
            "input_net_file": str(net_file.relative_to(PROJECT_ROOT)),
            "input_analysis_area_geojson": str(analysis_area_geojson.relative_to(PROJECT_ROOT)),
            "input_net_audit": str(net_audit.relative_to(PROJECT_ROOT)),
            "output_edges_geojson": str(edges_geojson.relative_to(PROJECT_ROOT)),
            "output_tls_geojson": str(tls_geojson.relative_to(PROJECT_ROOT)),
            "edge_feature_count": len(edge_features),
            "tls_feature_count": len(tls_features),
            "skipped_edge_count": edge_stats["skipped_edge_count"],
            "skipped_tls_count": skipped_tls_count,
            "internal_edge_count": edge_stats["internal_edge_count"],
            "passenger_allowed_edge_count": edge_stats["passenger_allowed_edge_count"],
            "emergency_candidate_edge_count": edge_stats["emergency_candidate_edge_count"],
            "coordinate_conversion_method": "sumolib.net.convertXY2LonLat",
            "step2_traffic_light_count": step2_tls_count,
            "tls_count_difference_from_step2": len(tls_features) - step2_tls_count,
            "warnings_summary": summarize_warnings(warnings),
            "notes": [
                "SUMO internal edges are preserved and marked with is_internal=true.",
                "TLS features are exported from the union of tlLogic/connection IDs and traffic_light junction IDs.",
                "allows_emergency_candidate uses emergency permission when present and falls back to passenger permission.",
                "Step 3 does not create HTML, select incident edges, regenerate net.xml, or download OSM.",
            ],
        }
        write_json(audit_output, audit)

        lines.extend(
            [
                "Status: PASS",
                f"edge_feature_count: {len(edge_features)}",
                f"tls_feature_count: {len(tls_features)}",
                f"skipped_edge_count: {edge_stats['skipped_edge_count']}",
                f"skipped_tls_count: {skipped_tls_count}",
                f"internal_edge_count: {edge_stats['internal_edge_count']}",
                f"passenger_allowed_edge_count: {edge_stats['passenger_allowed_edge_count']}",
                f"emergency_candidate_edge_count: {edge_stats['emergency_candidate_edge_count']}",
                f"step2_traffic_light_count: {step2_tls_count}",
                f"tls_count_difference_from_step2: {len(tls_features) - step2_tls_count}",
                f"warnings: {len(warnings)}",
                f"Wrote edges: {edges_geojson.relative_to(PROJECT_ROOT)}",
                f"Wrote TLS: {tls_geojson.relative_to(PROJECT_ROOT)}",
                f"Wrote audit: {audit_output.relative_to(PROJECT_ROOT)}",
            ]
        )
        log_lines(lines)
        print("\n".join(lines))
        return 0
    except (Step03Error, ImportError, RuntimeError, ValueError, OSError) as exc:
        lines.extend(["Status: FAIL", str(exc)])
        log_lines(lines)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
