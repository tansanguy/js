#!/usr/bin/env python3
"""Audit 09-series B04/B4 run conditions against the S1-forced profile."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = PROJECT_ROOT / "09 Compact Corridor Baseline"
RUNNER_DIR = PROJECT_ROOT / "09-1 B4 Optimization S1forced"
CANONICAL_PROFILE_NAME = "B04_B4_S1_FORCED_OPTIMIZATION"
CANONICAL = {
    "net_file": "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml",
    "background_route": "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml",
    "background_route_summary": "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.summary.json",
    "stage1_dir": "data_prepared/compact_v9/b4_stage1_s1forced",
    "firetruck_route": "data_prepared/compact_v9/routes/firetruck_to_seoul_station_front.rou.xml",
    "active_inputs": "configs/compact_v9_B04_B4_active_inputs.json",
    "signal_profile_csv": "09 Compact Corridor Baseline/tdata_signal/global_reality_signal_profiles.csv",
    "signal_mapping_csv": "09 Compact Corridor Baseline/tdata_signal/global_tls_a008_itst_mapping.csv",
    "signal_pipeline_summary_json": "09 Compact Corridor Baseline/tdata_signal/summaries/b04_global_reality_signal_summary.json",
    "route_geometry_recall_audit_json": "09 Compact Corridor Baseline/tdata_signal/route_geometry_recall_audit.json",
    "mainroad_lane_recall_audit_csv": "09 Compact Corridor Baseline/tdata_signal/mainroad_lane_recall_audit.csv",
    "route_internal_lane_alignment_audit_csv": "09 Compact Corridor Baseline/tdata_signal/route_internal_lane_alignment_audit.csv",
    "route_tls_projection_audit_csv": "09 Compact Corridor Baseline/tdata_signal/route_tls_projection_audit.csv",
}
CANONICAL_DECISION_VARIABLES = ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]
CANONICAL_METHODS = ["Random Search", "CMA-ES", "BO"]
CANONICAL_N = 15
CANONICAL_M = 50
CANONICAL_WORKERS_DEFAULT = 1
CANONICAL_OPTIMIZER_WEIGHTS = {"w_emv": 10.0, "w_veh": 1.0}
FIRETRUCK_ROUTE_XML = PROJECT_ROOT / "data_prepared/compact_v9/routes/firetruck_to_seoul_station_front.rou.xml"

@dataclass(frozen=True)
class Finding:
    severity: str
    check: str
    path: str
    detail: str


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"json_root_not_object:{rel(path)}")
    return payload


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def load_module(path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot_load_module:{rel(path)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def add_match(findings: list[Finding], ok: bool, check: str, path: Path, detail: str) -> None:
    findings.append(Finding("PASS" if ok else "FAIL", check, rel(path), detail))


def audit_manifest(findings: list[Finding]) -> None:
    manifest_path = PROJECT_ROOT / CANONICAL["active_inputs"]
    if not manifest_path.is_file():
        findings.append(Finding("FAIL", "manifest_exists", CANONICAL["active_inputs"], "canonical active-input manifest missing"))
        return
    payload = read_json(manifest_path)
    add_match(findings, payload.get("canonical_profile", CANONICAL_PROFILE_NAME) == CANONICAL_PROFILE_NAME, "manifest_profile", manifest_path, f"expected {CANONICAL_PROFILE_NAME}")
    for key in ["net_file", "background_route", "firetruck_route", "stage1_dir", "signal_profile_csv", "signal_mapping_csv"]:
        add_match(findings, payload.get(key) == CANONICAL[key], f"manifest_{key}", manifest_path, f"{payload.get(key)}")
    for key in [
        "signal_pipeline_summary_json",
        "route_geometry_recall_audit_json",
        "mainroad_lane_recall_audit_csv",
        "route_internal_lane_alignment_audit_csv",
        "route_tls_projection_audit_csv",
    ]:
        source = str(payload.get(key, ""))
        add_match(findings, source == CANONICAL[key], f"manifest_{key}", manifest_path, source)
        add_match(findings, bool(source) and (PROJECT_ROOT / source).is_file(), f"manifest_{key}_exists", manifest_path, source)
    for digest_key, path_key in [
        ("net_file_sha256", "net_file"),
        ("background_route_sha256", "background_route"),
        ("firetruck_route_sha256", "firetruck_route"),
    ]:
        source = PROJECT_ROOT / str(payload.get(path_key, ""))
        expected = sha256_file(source) if source.is_file() else ""
        add_match(findings, payload.get(digest_key) == expected, f"manifest_{digest_key}", manifest_path, f"{payload.get(digest_key)} expected {expected}")
    add_match(findings, payload.get("decision_variables", CANONICAL_DECISION_VARIABLES) == CANONICAL_DECISION_VARIABLES, "manifest_decision_variables", manifest_path, str(payload.get("decision_variables", CANONICAL_DECISION_VARIABLES)))
    weights = payload.get("optimizer_score_weights", CANONICAL_OPTIMIZER_WEIGHTS)
    add_match(findings, weights == CANONICAL_OPTIMIZER_WEIGHTS, "manifest_optimizer_weights", manifest_path, str(weights))

    b04_manifest_path = PROJECT_ROOT / "configs/compact_v9_B04_b0_manifest.json"
    if not b04_manifest_path.is_file():
        findings.append(Finding("FAIL", "b04_manifest_exists", rel(b04_manifest_path), "B04 manifest missing"))
        return
    b04_payload = read_json(b04_manifest_path)
    add_match(findings, b04_payload.get("active_net") == CANONICAL["net_file"], "b04_manifest_active_net", b04_manifest_path, str(b04_payload.get("active_net")))
    add_match(findings, b04_payload.get("background_route") == CANONICAL["background_route"], "b04_manifest_background_route", b04_manifest_path, str(b04_payload.get("background_route")))
    active_net = PROJECT_ROOT / CANONICAL["net_file"]
    active_net_sha = sha256_file(active_net) if active_net.is_file() else ""
    add_match(findings, b04_payload.get("active_net_sha256") == active_net_sha, "b04_manifest_active_net_sha256", b04_manifest_path, f"{b04_payload.get('active_net_sha256')} expected {active_net_sha}")
    for key in [
        "signal_pipeline_summary_json",
        "route_geometry_recall_audit_json",
        "mainroad_lane_recall_audit_csv",
        "route_internal_lane_alignment_audit_csv",
        "route_tls_projection_audit_csv",
    ]:
        add_match(findings, b04_payload.get(key) == CANONICAL[key], f"b04_manifest_{key}", b04_manifest_path, str(b04_payload.get(key)))


def audit_demand_summary(findings: list[Finding]) -> None:
    summary_path = PROJECT_ROOT / CANONICAL["background_route_summary"]
    if not summary_path.is_file():
        findings.append(Finding("FAIL", "demand_summary_exists", CANONICAL["background_route_summary"], "canonical demand summary missing"))
        return
    payload = read_json(summary_path)
    add_match(findings, payload.get("candidate") == "B04_ad_stage23_trigger", "demand_summary_candidate", summary_path, str(payload.get("candidate")))
    add_match(findings, payload.get("output_demand") == CANONICAL["background_route"], "demand_summary_output_demand", summary_path, str(payload.get("output_demand")))
    add_match(findings, payload.get("net_file") == CANONICAL["net_file"], "demand_summary_net_file", summary_path, str(payload.get("net_file")))
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    add_match(findings, settings.get("net_profile") == "global_reality_s1forced", "demand_summary_net_profile", summary_path, str(settings.get("net_profile")))


def audit_runner_defaults(findings: list[Finding]) -> None:
    runner_path = RUNNER_DIR / "run_b4_optimization_s1forced.py"
    if not runner_path.is_file():
        findings.append(Finding("FAIL", "canonical_runner_exists", rel(runner_path), "runner missing"))
        return
    module = load_module(runner_path, "b4_optimization_s1forced_audit")
    add_match(findings, rel(module.DEFAULT_NET) == CANONICAL["net_file"], "runner_default_net", runner_path, rel(module.DEFAULT_NET))
    add_match(findings, rel(module.DEFAULT_BACKGROUND_ROUTE) == CANONICAL["background_route"], "runner_default_background_route", runner_path, rel(module.DEFAULT_BACKGROUND_ROUTE))
    add_match(findings, rel(module.DEFAULT_STAGE1_DIR) == CANONICAL["stage1_dir"], "runner_default_stage1", runner_path, rel(module.DEFAULT_STAGE1_DIR))
    add_match(findings, rel(module.DEFAULT_ACTIVE_INPUTS) == CANONICAL["active_inputs"], "runner_default_active_inputs", runner_path, rel(module.DEFAULT_ACTIVE_INPUTS))
    add_match(findings, module.THETA_FIELDS == ["parameter_id", *CANONICAL_DECISION_VARIABLES], "runner_theta_fields", runner_path, str(module.THETA_FIELDS))
    add_match(findings, module.METHODS == CANONICAL_METHODS, "runner_methods", runner_path, str(module.METHODS))
    add_match(findings, module.DEFAULT_N == CANONICAL_N, "runner_default_n", runner_path, str(module.DEFAULT_N))
    add_match(findings, module.DEFAULT_M == CANONICAL_M, "runner_default_m", runner_path, str(module.DEFAULT_M))
    add_match(findings, module.DEFAULT_WORKERS == CANONICAL_WORKERS_DEFAULT, "runner_default_workers_safe", runner_path, str(module.DEFAULT_WORKERS))
    args = module.parse_args([])
    add_match(findings, args.w_emv == 10.0 and args.w_veh == 1.0, "runner_default_score_weights", runner_path, f"{args.w_emv}:{args.w_veh}")


def audit_runtime_defaults(findings: list[Finding]) -> None:
    runtime_path = PIPELINE_DIR / "b4_runtime.py"
    module = load_module(runtime_path, "b4_runtime_audit")
    add_match(findings, rel(module.B04_NET) == CANONICAL["net_file"], "runtime_default_net", runtime_path, rel(module.B04_NET))
    add_match(findings, rel(module.B04_AA_BACKGROUND_ROUTE) == CANONICAL["background_route"], "runtime_default_background_route", runtime_path, rel(module.B04_AA_BACKGROUND_ROUTE))
    add_match(findings, rel(module.STAGE1_DIR) == CANONICAL["stage1_dir"], "runtime_default_stage1", runtime_path, rel(module.STAGE1_DIR))
    add_match(findings, list(module.B4_DECISION_VARIABLES) == CANONICAL_DECISION_VARIABLES, "runtime_decision_variables", runtime_path, str(module.B4_DECISION_VARIABLES))
    add_match(findings, float(module.W_EMV) == 10.0 and float(module.W_VEH) == 1.0, "runtime_score_weight", runtime_path, f"{module.W_EMV}:{module.W_VEH}")


def audit_other_runner_defaults(findings: list[Finding]) -> None:
    mvp_path = PIPELINE_DIR / "run_b0_b4_signal_pipeline.py"
    mvp = load_module(mvp_path, "run_b0_b4_signal_pipeline_audit")
    add_match(findings, rel(mvp.B04_NET) == CANONICAL["net_file"], "mvp_runner_default_net", mvp_path, rel(mvp.B04_NET))
    add_match(findings, rel(mvp.B04_AA_BACKGROUND_ROUTE) == CANONICAL["background_route"], "mvp_runner_default_background_route", mvp_path, rel(mvp.B04_AA_BACKGROUND_ROUTE))
    mvp_stage1 = mvp.B4Stage1Inputs.load()
    add_match(findings, rel(mvp_stage1.stage1_dir) == CANONICAL["stage1_dir"], "mvp_runner_default_stage1", mvp_path, rel(mvp_stage1.stage1_dir))

    theta_bo_path = PIPELINE_DIR / "run_b4_theta_bo.py"
    theta_bo = load_module(theta_bo_path, "run_b4_theta_bo_audit")
    theta_args = theta_bo.parse_args(["--mock-eval"])
    add_match(findings, rel(theta_args.net_file) == CANONICAL["net_file"], "theta_bo_default_net", theta_bo_path, rel(theta_args.net_file))
    add_match(findings, rel(theta_args.background_route) == CANONICAL["background_route"], "theta_bo_default_background_route", theta_bo_path, rel(theta_args.background_route))
    add_match(findings, rel(theta_args.stage1_dir) == CANONICAL["stage1_dir"], "theta_bo_default_stage1", theta_bo_path, rel(theta_args.stage1_dir))

    final_path = PROJECT_ROOT / "10 Final Destination Validation/final_destination_validation.py"
    final = load_module(final_path, "final_destination_validation_audit")
    add_match(findings, rel(final.DEFAULT_NET) == CANONICAL["net_file"], "final_destination_default_net", final_path, rel(final.DEFAULT_NET))
    add_match(findings, rel(final.DEFAULT_BACKGROUND_ROUTE) == CANONICAL["background_route"], "final_destination_default_background_route", final_path, rel(final.DEFAULT_BACKGROUND_ROUTE))
    add_match(findings, rel(final.DEFAULT_BASE_STAGE1_DIR) == CANONICAL["stage1_dir"], "final_destination_default_stage1", final_path, rel(final.DEFAULT_BASE_STAGE1_DIR))

    stage1_builder_path = PIPELINE_DIR / "build_b4_stage1_from_b04_run.py"
    stage1_builder = load_module(stage1_builder_path, "build_b4_stage1_from_b04_run_audit")
    add_match(findings, rel(stage1_builder.DEFAULT_NET) == CANONICAL["net_file"], "stage1_builder_default_net", stage1_builder_path, rel(stage1_builder.DEFAULT_NET))
    add_match(findings, rel(stage1_builder.DEFAULT_DEMAND) == CANONICAL["background_route"], "stage1_builder_default_demand", stage1_builder_path, rel(stage1_builder.DEFAULT_DEMAND))
    add_match(findings, rel(stage1_builder.DEFAULT_STAGE1_DIR) == CANONICAL["stage1_dir"], "stage1_builder_default_stage1", stage1_builder_path, rel(stage1_builder.DEFAULT_STAGE1_DIR))


def audit_baseline_defaults(findings: list[Finding]) -> None:
    baseline_path = PIPELINE_DIR / "b04_baseline_pipeline.py"
    baseline = load_module(baseline_path, "b04_baseline_pipeline_audit")
    add_match(findings, getattr(baseline, "B04_LATEST_CANDIDATE", "") == "B04_ad_stage23_trigger", "baseline_latest_candidate", baseline_path, str(getattr(baseline, "B04_LATEST_CANDIDATE", "")))
    add_match(findings, "B04_ad_stage23_trigger" in baseline.CANDIDATES, "baseline_stage23_candidate_registered", baseline_path, str("B04_ad_stage23_trigger" in baseline.CANDIDATES))

    stage1_path = PIPELINE_DIR / "b4_stage1_pipeline.py"
    stage1 = load_module(stage1_path, "b4_stage1_pipeline_audit")
    add_match(findings, stage1.B4_PRIMARY_CANDIDATE == "B04_ad_stage23_trigger", "stage1_primary_candidate", stage1_path, str(stage1.B4_PRIMARY_CANDIDATE))
    measurement_source = str(stage1.B4_MEASUREMENT_SOURCE_CANDIDATE)
    add_match(findings, measurement_source == "B04_ad_stage23_trigger", "stage1_measurement_source_candidate", stage1_path, measurement_source)
    add_match(findings, rel(stage1.STAGE1_DIR) == CANONICAL["stage1_dir"], "stage1_output_dir", stage1_path, rel(stage1.STAGE1_DIR))


def audit_signal_route_artifacts(findings: list[Finding]) -> None:
    summary_path = PROJECT_ROOT / CANONICAL["signal_pipeline_summary_json"]
    if not summary_path.is_file():
        findings.append(Finding("FAIL", "signal_pipeline_summary_exists", rel(summary_path), "summary missing"))
        return
    payload = read_json(summary_path)
    add_match(findings, payload.get("output_net") == CANONICAL["net_file"], "signal_pipeline_output_net", summary_path, str(payload.get("output_net")))
    add_match(findings, payload.get("route_tls_projection_status") == "PASS", "signal_pipeline_route_tls_projection_status", summary_path, str(payload.get("route_tls_projection_status")))
    add_match(findings, int(payload.get("route_tls_projection_fail_count", -1)) == 0, "signal_pipeline_route_tls_projection_fail_count", summary_path, str(payload.get("route_tls_projection_fail_count")))

    geometry = payload.get("route_geometry_recall") if isinstance(payload.get("route_geometry_recall"), dict) else {}
    add_match(findings, geometry.get("status") == "PASS", "signal_pipeline_route_geometry_recall_status", summary_path, str(geometry.get("status")))
    add_match(findings, geometry.get("route_edges_match_reference") is True, "signal_pipeline_route_edges_match_reference", summary_path, str(geometry.get("route_edges_match_reference")))
    add_match(findings, int(geometry.get("lane_fail_count", -1)) == 0, "signal_pipeline_lane_fail_count", summary_path, str(geometry.get("lane_fail_count")))
    add_match(findings, geometry.get("route_internal_lane_status") == "PASS", "signal_pipeline_internal_lane_status", summary_path, str(geometry.get("route_internal_lane_status")))
    add_match(findings, int(geometry.get("route_internal_lane_missing_count", -1)) == 0, "signal_pipeline_internal_lane_missing_count", summary_path, str(geometry.get("route_internal_lane_missing_count")))

    geometry_path = PROJECT_ROOT / CANONICAL["route_geometry_recall_audit_json"]
    geometry_payload = read_json(geometry_path)
    add_match(findings, geometry_payload.get("status") == "PASS", "route_geometry_recall_audit_status", geometry_path, str(geometry_payload.get("status")))
    add_match(findings, geometry_payload.get("route_internal_lane_status") == "PASS", "route_internal_lane_audit_status", geometry_path, str(geometry_payload.get("route_internal_lane_status")))

    for key, check_name in [
        ("mainroad_lane_recall_audit_csv", "mainroad_lane_recall_audit_all_pass"),
        ("route_internal_lane_alignment_audit_csv", "route_internal_lane_alignment_audit_all_pass"),
    ]:
        path = PROJECT_ROOT / CANONICAL[key]
        rows = read_csv_rows(path)
        bad = [row for row in rows if row.get("status") != "PASS"]
        add_match(findings, bool(rows) and not bad, check_name, path, f"rows={len(rows)} bad={len(bad)}")

    tls_path = PROJECT_ROOT / CANONICAL["route_tls_projection_audit_csv"]
    tls_rows = read_csv_rows(tls_path)
    tls_failures = [row for row in tls_rows if row.get("status") == "FAIL"]
    add_match(findings, bool(tls_rows) and not tls_failures, "route_tls_projection_audit_no_fail", tls_path, f"rows={len(tls_rows)} fail={len(tls_failures)}")


def firetruck_route_edges() -> list[str]:
    root = ET.parse(FIRETRUCK_ROUTE_XML).getroot()
    route = root.find(".//route")
    if route is None:
        return []
    return [edge for edge in str(route.get("edges", "")).split() if edge]


def audit_canonical_net_priority(findings: list[Finding]) -> None:
    net_path = PROJECT_ROOT / CANONICAL["net_file"]
    if not net_path.is_file():
        findings.append(Finding("FAIL", "canonical_net_exists", CANONICAL["net_file"], "canonical net missing"))
        return
    route_pairs = set(zip(firetruck_route_edges(), firetruck_route_edges()[1:]))
    minor_pairs: list[str] = []
    root = ET.parse(net_path).getroot()
    active_inputs = read_json(PROJECT_ROOT / CANONICAL["active_inputs"])
    add_match(findings, active_inputs.get("net_file_sha256") == sha256_file(net_path), "canonical_net_sha256", net_path, str(active_inputs.get("net_file_sha256")))
    for conn in root.findall("connection"):
        pair = (conn.get("from", ""), conn.get("to", ""))
        if pair not in route_pairs or conn.get("tl"):
            continue
        if conn.get("state") in {"m", "o"}:
            minor_pairs.append(f"{pair[0]}->{pair[1]} lane {conn.get('fromLane', '')}->{conn.get('toLane', '')} state={conn.get('state')}")
    add_match(
        findings,
        not minor_pairs,
        "canonical_net_firetruck_uncontrolled_priority",
        net_path,
        "; ".join(minor_pairs) if minor_pairs else "all uncontrolled firetruck route connections are priority state",
    )

def audit() -> dict[str, Any]:
    findings: list[Finding] = []
    audit_manifest(findings)
    audit_demand_summary(findings)
    audit_runner_defaults(findings)
    audit_runtime_defaults(findings)
    audit_other_runner_defaults(findings)
    audit_baseline_defaults(findings)
    audit_signal_route_artifacts(findings)
    audit_canonical_net_priority(findings)
    summary = {
        "schema": "compact_v9_B4_09_run_condition_audit.v1",
        "canonical_profile": CANONICAL_PROFILE_NAME,
        "canonical": CANONICAL,
        "decision_variables": CANONICAL_DECISION_VARIABLES,
        "optimizer_score": "(10/11) * delay_A + (1/11) * delay_N",
        "n": CANONICAL_N,
        "m": CANONICAL_M,
        "workers_default": CANONICAL_WORKERS_DEFAULT,
        "workers_runbook": 6,
        "status": "FAIL" if any(item.severity == "FAIL" for item in findings) else "PASS",
        "findings": [item.__dict__ for item in findings],
    }
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit 09-series B04/B4 run-condition consistency.")
    parser.add_argument("--json", action="store_true", help="Emit the full JSON report.")
    parser.add_argument("--fail-on-warn", action="store_true", help="Reserved for compatibility; canonical audit should not emit warnings.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit()
    warnings = sum(1 for item in report["findings"] if item["severity"] == "WARN")
    infos = sum(1 for item in report["findings"] if item["severity"] == "INFO")
    failures = sum(1 for item in report["findings"] if item["severity"] == "FAIL")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{report['status']} profile={report['canonical_profile']} failures={failures} warnings={warnings} infos={infos}")
        for item in report["findings"]:
            if item["severity"] != "PASS":
                print(f"{item['severity']} {item['check']} {item['path']} {item['detail']}")
    if failures or (args.fail_on_warn and warnings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
