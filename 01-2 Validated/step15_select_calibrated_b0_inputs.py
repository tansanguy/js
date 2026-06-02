#!/usr/bin/env python3
from __future__ import annotations

import argparse

from validated_pipeline import CALIBRATED_MANIFEST, PROJECT_ROOT, project_path, read_csv, read_json, rel, selection_score, write_csv, write_json


DEFAULT_SWEEP = PROJECT_ROOT / "results/metrics/validated_b0_tls_boundary_sweep/sweep_summary.csv"
DEFAULT_SELECTION_JSON = PROJECT_ROOT / "results/metrics/validated_b0_tls_boundary_sweep/selection_summary.json"
DEFAULT_RECOMMENDATION_CSV = PROJECT_ROOT / "results/metrics/validated_b0_tls_boundary_sweep/recommended_calibrated_inputs.csv"


def float_cell(row: dict[str, str], key: str, default: float = 999999.0) -> float:
    try:
        return float(row.get(key) or default)
    except ValueError:
        return default


def candidate_is_usable(row: dict[str, str]) -> bool:
    return (
        row.get("runner_returncode") == "0"
        and row.get("sumo_exit_code") == "0"
        and row.get("route_error_count") in {"", "0", "0.0"}
        and float_cell(row, "background_teleported", 999999.0) < 10.0
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Select the best calibrated B0 TLS/boundary candidate.")
    parser.add_argument("--sweep-summary", default=str(DEFAULT_SWEEP))
    parser.add_argument("--output-manifest", default=str(CALIBRATED_MANIFEST))
    parser.add_argument("--selection-json", default=str(DEFAULT_SELECTION_JSON))
    parser.add_argument("--recommendation-csv", default=str(DEFAULT_RECOMMENDATION_CSV))
    args = parser.parse_args()

    rows = read_csv(project_path(args.sweep_summary))
    if not rows:
        raise SystemExit("empty sweep summary")
    ranked = sorted(rows, key=lambda row: (0 if candidate_is_usable(row) else 1, selection_score(row)))
    best = ranked[0]
    source_manifest = project_path(best["manifest"])
    manifest = read_json(source_manifest)
    manifest["schema"] = "validated_calibrated_b0_manifest.v1"
    manifest["notes"] = "Selected calibrated B0 baseline manifest from TLS/boundary sweep. B1/B2 control logic remains unchanged."
    manifest["validated_calibrated_selection"] = {
        "selected_candidate_id": best.get("candidate_id", ""),
        "sweep_summary": rel(project_path(args.sweep_summary)),
        "selection_score": selection_score(best),
        "usable_candidate": candidate_is_usable(best),
        "speed_mae_kmh": best.get("speed_mae_kmh", ""),
        "edge_speed_mae_kmh": best.get("edge_speed_mae_kmh", ""),
        "geh_pass_warn_ratio": best.get("geh_pass_warn_ratio", ""),
        "background_teleported": best.get("background_teleported", ""),
        "remaining_vehicle_count": best.get("remaining_vehicle_count", ""),
        "s15_s22_over_open_edge_count": best.get("s15_s22_over_open_edge_count", ""),
    }
    output_manifest = project_path(args.output_manifest)
    write_json(output_manifest, manifest)
    write_csv(
        project_path(args.recommendation_csv),
        [best],
        [
            "candidate_id",
            "net_file",
            "route_file",
            "manifest",
            "background_teleported",
            "remaining_vehicle_count",
            "geh_pass_warn_ratio",
            "speed_mae_kmh",
            "edge_speed_mae_kmh",
            "s15_s22_over_open_edge_count",
            "selection_score",
        ],
    )
    write_json(
        project_path(args.selection_json),
        {
            "schema": "validated_calibrated_b0_selection.v1",
            "sweep_summary": rel(project_path(args.sweep_summary)),
            "output_manifest": rel(output_manifest),
            "best_candidate": best,
            "usable_candidate": candidate_is_usable(best),
            "candidate_count": len(rows),
        },
    )
    print(f"selected {best.get('candidate_id', '')} manifest={rel(output_manifest)} usable={candidate_is_usable(best)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
