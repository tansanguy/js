#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from validated_pipeline import (
    DEFAULT_REPAIRED_NET,
    DEFAULT_SWEEP_SUMMARY,
    DEFAULT_VALIDATED_MANIFEST,
    PROJECT_ROOT,
    needs_downstream_or_tls_calibration,
    project_path,
    read_csv,
    rel,
    selection_score,
    validated_manifest_payload,
    write_csv,
    write_json,
)


def select_best(rows: list[dict[str, str]]) -> dict[str, str]:
    if not rows:
        raise SystemExit("no sweep rows available")
    completed = [row for row in rows if row.get("results_csv")]
    candidates = completed or rows
    return min(candidates, key=selection_score)


def main() -> int:
    parser = argparse.ArgumentParser(description="Select validated net/demand inputs from B0 scale sweep results.")
    parser.add_argument("--sweep-summary", default=str(DEFAULT_SWEEP_SUMMARY))
    parser.add_argument("--net", default=str(DEFAULT_REPAIRED_NET))
    parser.add_argument("--manifest-output", default=str(DEFAULT_VALIDATED_MANIFEST))
    parser.add_argument("--selection-dir", default=str(PROJECT_ROOT / "results/metrics/validated_b0_scale_sweep"))
    args = parser.parse_args()

    rows = read_csv(project_path(args.sweep_summary))
    best = select_best(rows)
    demand_file = project_path(best["route_file"])
    warmup_scale = float(best["warmup_scale"])
    sustain_scale = float(best["sustain_scale"])
    needs_downstream = needs_downstream_or_tls_calibration(best)
    manifest = validated_manifest_payload(
        project_path(args.net),
        demand_file,
        warmup_scale,
        sustain_scale,
        notes="Selected validated manifest from lane-repaired B0 scale sweep.",
    )
    manifest["validated_selection"] = {
        "selected_scale_label": best.get("scale_label", ""),
        "selection_score": selection_score(best),
        "sweep_summary": rel(project_path(args.sweep_summary)),
        "validation_summary_json": best.get("validation_summary_json", ""),
        "needs_downstream_or_tls_calibration": needs_downstream,
    }
    manifest_output = project_path(args.manifest_output)
    write_json(manifest_output, manifest)
    selection_dir = project_path(args.selection_dir)
    selection_summary: dict[str, Any] = {
        "schema": "validated_input_selection.v1",
        "selected": best,
        "selection_score": selection_score(best),
        "manifest": rel(manifest_output),
        "needs_downstream_or_tls_calibration": needs_downstream,
    }
    summary_json = selection_dir / "selection_summary.json"
    recommended_csv = selection_dir / "recommended_validated_inputs.csv"
    write_json(summary_json, selection_summary)
    write_csv(
        recommended_csv,
        [
            {
                "active_net": rel(project_path(args.net)),
                "background_route": rel(demand_file),
                "warmup_scale": warmup_scale,
                "sustain_scale": sustain_scale,
                "manifest": rel(manifest_output),
                "selection_score": selection_score(best),
                "needs_downstream_or_tls_calibration": needs_downstream,
            }
        ],
        [
            "active_net",
            "background_route",
            "warmup_scale",
            "sustain_scale",
            "manifest",
            "selection_score",
            "needs_downstream_or_tls_calibration",
        ],
    )
    print(f"selected {best.get('scale_label', '')} manifest={rel(manifest_output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
