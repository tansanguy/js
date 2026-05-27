#!/usr/bin/env python3
"""Download fixed OSM bbox data and build a SUMO net.xml for Step 2."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.net_utils import (  # noqa: E402
    count_net_elements,
    download_osm_bbox,
    file_size_mb,
    find_executable,
    load_json,
    run_netconvert,
    sha256_file,
    sumo_version,
    validate_bbox_wgs84,
    validate_osm_xml,
    validate_sumo_net_xml,
    write_json,
)


CONFIG_PATH = PROJECT_ROOT / "config/map_config.yaml"
LOG_PATH = PROJECT_ROOT / "outputs/logs/step02_build_map.log"
DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
FALLBACK_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


class Step02Error(RuntimeError):
    """Expected Step 2 failure with user-facing error text."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_lines(lines: list[str]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def require_field(data: dict[str, Any], dotted_path: str) -> Any:
    current: Any = data
    for key in dotted_path.split("."):
        if not isinstance(current, dict) or key not in current:
            raise Step02Error(f"ERROR: missing required config field: {dotted_path}")
        current = current[key]
    return current


def load_yaml_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        raise Step02Error(f"ERROR: config file not found: {CONFIG_PATH.relative_to(PROJECT_ROOT)}")
    try:
        import yaml
    except ImportError as exc:
        raise Step02Error(
            "ERROR: PyYAML is required to read config/map_config.yaml. "
            "Install it with: python3 -m pip install PyYAML"
        ) from exc

    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise Step02Error("ERROR: config/map_config.yaml must contain a YAML mapping")
    return config


def rel_path(path_text: str) -> Path:
    return PROJECT_ROOT / path_text


def backup_existing_file(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def build_netconvert_command(osm_file: Path, net_file: Path) -> list[str]:
    netconvert = find_executable("netconvert")
    return [
        netconvert,
        "--osm-files",
        str(osm_file),
        "--output-file",
        str(net_file),
        "--tls.guess",
        "true",
        "--tls.join",
        "true",
        "--junctions.join",
        "true",
        "--geometry.remove",
        "true",
        "--remove-edges.isolated",
        "true",
        "--no-turnarounds",
        "true",
    ]


def warnings_summary(stderr_text: str) -> dict[str, Any]:
    warning_lines = [
        line.strip()
        for line in stderr_text.splitlines()
        if "warning" in line.lower()
    ]
    return {
        "warning_count": len(warning_lines),
        "sample": warning_lines[:20],
    }


def write_command_file(path: Path, command: list[str], notes: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Step 2 netconvert command draft",
        "# These options are first-pass values, not final fixed research parameters.",
        "# Re-run Step 2 with adjusted options if net_audit.json or sumo-gui inspection shows issues.",
        "",
        shlex.join(command),
        "",
        "# Notes",
    ]
    lines.extend(f"- {note}" for note in notes)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Step 2 OSM and SUMO network files.")
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Back up existing OSM file and download fresh OSM data.",
    )
    parser.add_argument(
        "--overpass-url",
        action="append",
        default=None,
        help="Overpass API interpreter URL.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=180,
        help="OSM download timeout in seconds.",
    )
    return parser


def download_with_fallbacks(
    bbox: dict[str, float],
    output_path: Path,
    urls: list[str],
    timeout_sec: int,
    lines: list[str],
) -> tuple[str, bool]:
    failures: list[str] = []
    for url in urls:
        try:
            lines.append(f"Downloading OSM from Overpass API: {url}")
            download_osm_bbox(
                bbox=bbox,
                output_path=output_path,
                overpass_url=url,
                timeout_sec=timeout_sec,
                verify_ssl=True,
            )
            return url, False
        except RuntimeError as exc:
            failures.append(str(exc))
            if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                continue

            lines.append(
                "Strict SSL download failed due local certificate verification; "
                "retrying same endpoint with SSL verification disabled."
            )
            try:
                download_osm_bbox(
                    bbox=bbox,
                    output_path=output_path,
                    overpass_url=url,
                    timeout_sec=timeout_sec,
                    verify_ssl=False,
                )
                return url, True
            except RuntimeError as insecure_exc:
                failures.append(str(insecure_exc))

    joined = " | ".join(failures)
    raise Step02Error(f"ERROR: failed to download OSM data from Overpass API: {joined}")


def main() -> int:
    args = build_parser().parse_args()
    lines = [
        "Step 2 OSM download and SUMO net build",
        "======================================",
        f"Project root: {PROJECT_ROOT}",
        "Policy: auto_download_once_then_reuse",
        "Netconvert options: first-pass draft; adjust after audit and sumo-gui inspection if needed.",
    ]
    downloaded_at: str | None = None
    force_backup: str | None = None
    download_url_used: str | None = None
    download_ssl_verify_disabled = False

    try:
        config = load_yaml_config()
        meta_path = rel_path(require_field(config, "outputs.analysis_area_meta"))
        if not meta_path.is_file():
            raise Step02Error(
                f"ERROR: analysis area meta not found: {meta_path.relative_to(PROJECT_ROOT)}"
            )

        meta = load_json(meta_path)
        if "bbox_wgs84" not in meta:
            raise Step02Error("ERROR: missing required meta field: bbox_wgs84")
        bbox_wgs84 = validate_bbox_wgs84(meta["bbox_wgs84"])

        osm_file = rel_path(require_field(config, "outputs.osm_file"))
        net_file = rel_path(require_field(config, "outputs.net_file"))
        command_file = rel_path(require_field(config, "outputs.netconvert_command"))
        audit_file = rel_path(require_field(config, "outputs.net_audit"))
        manifest_file = rel_path(require_field(config, "outputs.map_manifest"))

        lines.append(f"bbox_wgs84: {bbox_wgs84}")

        if osm_file.exists() and not args.force_download:
            lines.append(f"OSM file exists; reuse without download: {osm_file.relative_to(PROJECT_ROOT)}")
            downloaded_at = datetime.fromtimestamp(osm_file.stat().st_mtime, timezone.utc).isoformat()
        else:
            if osm_file.exists() and args.force_download:
                backup_path = backup_existing_file(osm_file)
                force_backup = str(backup_path.relative_to(PROJECT_ROOT))
                lines.append(f"Force download enabled; backed up existing OSM file: {force_backup}")
            overpass_urls = args.overpass_url or FALLBACK_OVERPASS_URLS
            download_url_used, download_ssl_verify_disabled = download_with_fallbacks(
                bbox=bbox_wgs84,
                output_path=osm_file,
                urls=overpass_urls,
                timeout_sec=args.timeout_sec,
                lines=lines,
            )
            downloaded_at = utc_now()
            lines.append(f"Downloaded OSM: {osm_file.relative_to(PROJECT_ROOT)}")

        osm_counts = validate_osm_xml(osm_file)
        lines.append(f"OSM counts: {osm_counts}")

        net_file.parent.mkdir(parents=True, exist_ok=True)
        command = build_netconvert_command(osm_file, net_file)
        notes = [
            "Options are first-pass values for Seoul downtown signal-network preservation.",
            "Do not treat these options as final fixed parameters until net_audit and sumo-gui checks pass.",
            "Step 2 does not clip by analysis_ellipse and does not apply bbox buffer again.",
        ]
        write_command_file(command_file, command, notes)

        completed = run_netconvert(command)
        if completed.returncode != 0:
            lines.extend(
                [
                    f"netconvert return code: {completed.returncode}",
                    "netconvert stderr:",
                    completed.stderr.strip(),
                ]
            )
            raise Step02Error(f"ERROR: netconvert failed with return code {completed.returncode}")

        validate_sumo_net_xml(net_file)
        net_counts = count_net_elements(net_file)
        if net_counts["edge_count"] <= 0:
            raise Step02Error("ERROR: edge_count is 0; generated net is invalid")
        if net_counts["junction_count"] <= 0:
            raise Step02Error("ERROR: junction_count is 0; generated net is invalid")
        if net_counts["lane_count"] <= 0:
            raise Step02Error("ERROR: lane_count is 0; generated net is invalid")
        if net_counts["traffic_light_count"] <= 0:
            raise Step02Error(
                "ERROR: traffic_light_count is 0; generated net is not suitable for signal-control study"
            )

        generated_at = utc_now()
        command_text = shlex.join(command)
        audit = {
            "generated_at": generated_at,
            "input_osm_file": str(osm_file.relative_to(PROJECT_ROOT)),
            "output_net_file": str(net_file.relative_to(PROJECT_ROOT)),
            "bbox_wgs84": bbox_wgs84,
            "netconvert_command": command_text,
            "netconvert_option_status": "first_pass_draft_adjust_after_net_audit_and_sumo_gui",
            "netconvert_return_code": completed.returncode,
            "net_file_exists": net_file.is_file(),
            "net_file_size_mb": file_size_mb(net_file),
            "edge_count": net_counts["edge_count"],
            "junction_count": net_counts["junction_count"],
            "traffic_light_count": net_counts["traffic_light_count"],
            "lane_count": net_counts["lane_count"],
            "warnings_summary": warnings_summary(completed.stderr),
            "notes": [
                "bbox_wgs84 was read from Step 1 analysis_area_meta.json without recalculation.",
                "bbox_buffer_m was not applied again in Step 2.",
                "analysis_ellipse clipping was not performed.",
                "Inspect the net in sumo-gui before treating the netconvert draft options as stable.",
            ],
        }
        write_json(audit_file, audit)

        manifest = {
            "bbox_wgs84": bbox_wgs84,
            "osm_file": str(osm_file.relative_to(PROJECT_ROOT)),
            "osm_file_sha256": sha256_file(osm_file),
            "net_file": str(net_file.relative_to(PROJECT_ROOT)),
            "net_file_sha256": sha256_file(net_file),
            "downloaded_at": downloaded_at,
            "generated_at": generated_at,
            "sumo_version": sumo_version(),
            "netconvert_command": command_text,
            "netconvert_option_status": "first_pass_draft_adjust_after_net_audit_and_sumo_gui",
            "source_policy": "auto_download_once_then_reuse",
            "force_download_used": args.force_download,
            "force_download_backup": force_backup,
            "download_url_used": download_url_used,
            "download_ssl_verify_disabled": download_ssl_verify_disabled,
        }
        write_json(manifest_file, manifest)

        lines.extend(
            [
                "Status: PASS",
                f"Wrote OSM: {osm_file.relative_to(PROJECT_ROOT)} ({file_size_mb(osm_file):.3f} MB)",
                f"Wrote net: {net_file.relative_to(PROJECT_ROOT)} ({file_size_mb(net_file):.3f} MB)",
                f"edge_count: {net_counts['edge_count']}",
                f"junction_count: {net_counts['junction_count']}",
                f"traffic_light_count: {net_counts['traffic_light_count']}",
                f"lane_count: {net_counts['lane_count']}",
                f"Wrote command: {command_file.relative_to(PROJECT_ROOT)}",
                f"Wrote audit: {audit_file.relative_to(PROJECT_ROOT)}",
                f"Wrote manifest: {manifest_file.relative_to(PROJECT_ROOT)}",
            ]
        )
        log_lines(lines)
        print("\n".join(lines))
        return 0
    except (Step02Error, ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
        lines.extend(["Status: FAIL", str(exc)])
        log_lines(lines)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
