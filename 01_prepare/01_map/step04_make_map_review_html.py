#!/usr/bin/env python3
"""Create Leaflet map review HTML for manual SUMO edge selection."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.html_utils import (  # noqa: E402
    build_selected_edges_schema,
    load_json,
    render_map_review_html,
    write_text,
)


CONFIG_PATH = PROJECT_ROOT / "config/map_config.yaml"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step04_make_map_review_html.log"


class Step04Error(RuntimeError):
    """Expected Step 4 failure with user-facing error text."""


def log_lines(lines: list[str]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def require_field(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for key in dotted_path.split("."):
        if not isinstance(current, dict) or key not in current:
            raise Step04Error(f"ERROR: missing required config field: {dotted_path}")
        current = current[key]
    return current


def load_yaml_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        raise Step04Error(f"ERROR: config file not found: {CONFIG_PATH.relative_to(PROJECT_ROOT)}")
    try:
        import yaml
    except ImportError as exc:
        raise Step04Error(
            "ERROR: PyYAML is required to read config/map_config.yaml. "
            "Install it with: python3 -m pip install PyYAML"
        ) from exc

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise Step04Error("ERROR: config/map_config.yaml must contain a YAML mapping")
    return config


def rel_path(path_text: str) -> Path:
    return PROJECT_ROOT / path_text


def load_feature_collection(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise Step04Error(f"ERROR: {label} not found: {path.relative_to(PROJECT_ROOT)}")
    try:
        payload = load_json(path)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise Step04Error(f"ERROR: failed to parse GeoJSON: {path.relative_to(PROJECT_ROOT)}") from exc

    if payload.get("type") != "FeatureCollection":
        raise Step04Error(f"ERROR: invalid GeoJSON FeatureCollection: {path.relative_to(PROJECT_ROOT)}")
    features = payload.get("features")
    if not isinstance(features, list):
        raise Step04Error(f"ERROR: GeoJSON features must be a list: {path.relative_to(PROJECT_ROOT)}")
    return payload


def relative_from_html(html_path: Path, target_path: Path) -> str:
    return Path(os.path.relpath(target_path, start=html_path.parent)).as_posix()


def bbox_center_from_meta(meta_path: Path) -> tuple[list[float], int]:
    if not meta_path.is_file():
        return [37.5616815, 126.9934095], 14
    meta = load_json(meta_path)
    bbox = meta.get("bbox_wgs84", {})
    try:
        lat = (float(bbox["min_lat"]) + float(bbox["max_lat"])) / 2.0
        lon = (float(bbox["min_lon"]) + float(bbox["max_lon"])) / 2.0
    except (KeyError, TypeError, ValueError):
        return [37.5616815, 126.9934095], 14
    return [lat, lon], 14


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    lines = [
        "Step 4 map review HTML generation",
        "=================================",
        f"Project root: {PROJECT_ROOT}",
        "Policy: create review HTML only; do not create selected_edges.json automatically.",
        "layout: flex_leaflet_stable",
        "leaflet_css_fallback: enabled",
        "layer_visibility_debug: enabled",
        "debug_layers: first_edge_marker, first_10_edges, analysis_overlay, tls_10",
    ]

    try:
        config = load_yaml_config()
        analysis_path = rel_path(require_field(config, "outputs.analysis_area_geojson"))
        edges_path = rel_path(require_field(config, "outputs.sumo_edges_geojson"))
        tls_path = rel_path(require_field(config, "outputs.sumo_tls_geojson"))
        step03_audit_path = rel_path(require_field(config, "outputs.step03_geojson_audit"))
        meta_path = rel_path(require_field(config, "outputs.analysis_area_meta"))
        html_path = rel_path(require_field(config, "outputs.map_review_html"))
        schema_path = rel_path(require_field(config, "outputs.selected_edges_schema"))

        analysis = load_feature_collection(analysis_path, "analysis area GeoJSON")
        edges = load_feature_collection(edges_path, "SUMO edges GeoJSON")
        tls = load_feature_collection(tls_path, "SUMO TLS GeoJSON")
        step03_audit = load_json(step03_audit_path)

        edge_count = len(edges["features"])
        tls_count = len(tls["features"])
        if edge_count == 0:
            raise Step04Error("ERROR: edge Feature count is 0")
        if tls_count == 0:
            raise Step04Error("ERROR: TLS Feature count is 0")

        initial_center, initial_zoom = bbox_center_from_meta(meta_path)
        context = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "review_html_path": str(html_path.relative_to(PROJECT_ROOT)),
            "localhost_url": f"http://localhost:8000/{html_path.relative_to(PROJECT_ROOT).as_posix()}",
            "initial_center": initial_center,
            "initial_zoom": initial_zoom,
            "counts": {
                "analysis_feature_count": len(analysis["features"]),
                "edge_feature_count": edge_count,
                "tls_feature_count": tls_count,
            },
            "step03_audit_summary": {
                "edge_feature_count": step03_audit.get("edge_feature_count"),
                "tls_feature_count": step03_audit.get("tls_feature_count"),
                "warnings": step03_audit.get("warnings_summary", {}).get("warning_count"),
            },
            "paths": {
                "analysis_area_geojson": relative_from_html(html_path, analysis_path),
                "sumo_edges_geojson": relative_from_html(html_path, edges_path),
                "sumo_tls_geojson": relative_from_html(html_path, tls_path),
            },
        }

        html = render_map_review_html(context)
        try:
            write_text(html_path, html)
        except OSError as exc:
            raise Step04Error(f"ERROR: failed to write HTML file: {html_path.relative_to(PROJECT_ROOT)}") from exc

        write_json(schema_path, build_selected_edges_schema())

        lines.extend(
            [
                "Status: PASS",
                f"analysis_feature_count: {len(analysis['features'])}",
                f"edge_feature_count: {edge_count}",
                f"tls_feature_count: {tls_count}",
                f"Wrote HTML: {html_path.relative_to(PROJECT_ROOT)}",
                f"Wrote schema: {schema_path.relative_to(PROJECT_ROOT)}",
                "CORS fallback: run python3 -m http.server 8000 at project root if file:// fetch is blocked.",
            ]
        )
        log_lines(lines)
        print("\n".join(lines))
        return 0
    except (Step04Error, OSError, ValueError) as exc:
        lines.extend(["Status: FAIL", str(exc)])
        log_lines(lines)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
