#!/usr/bin/env python3
"""Extract trajectory data from SUMO simulation results."""

import argparse
import json
from pathlib import Path

from config import (
    PARAMETER_INPUT_SIM_LATEST,
    PARAMETER_INPUT_SIM_DIR,
    RUNS_DIR,
    HTML_OUTPUT_DIR,
)
from utils import (
    load_experiment_results_csv,
    filter_results_by_mode,
    extract_emergency_metrics,
)


def load_latest_run_id(latest_json_path: Path) -> str:
    """Load latest run ID from latest.json pointer."""
    if not latest_json_path.exists():
        raise FileNotFoundError(f"Latest pointer not found: {latest_json_path}")
    
    data = json.loads(latest_json_path.read_text(encoding="utf-8"))
    return data.get("run_id", "")


def extract_trajectory_summary(
    results_csv: Path,
    mode_filter: str | None = None,
) -> dict[str, any]:
    """
    Extract summary of trajectories from results CSV.
    
    Args:
        results_csv: Path to experiment_results.csv
        mode_filter: Filter by mode (B00, B0, B2) or None for all
        
    Returns:
        Dictionary with mode -> list of trajectory summaries
    """
    rows = load_experiment_results_csv(results_csv)
    
    summary = {}
    modes = {mode_filter} if mode_filter else {"B00", "B0", "B2"}
    
    for mode in modes:
        mode_rows = filter_results_by_mode(rows, mode)
        mode_rows.sort(
            key=lambda r: (r.get("repeat_id", ""), r.get("parameter_id", ""))
        )
        
        trajectories = []
        for row in mode_rows:
            metrics = extract_emergency_metrics(row)
            trajectories.append({
                "parameter_id": metrics["parameter_id"],
                "repeat_id": metrics["repeat_id"],
                "travel_time_sec": round(metrics["travel_time_sec"], 2),
                "avg_speed_kmh": round(metrics["avg_speed_kmh"], 2),
                "arrived": metrics["arrived"],
                "teleported": metrics["teleported"],
                "status": metrics["final_status"],
                "warning": metrics["warning_reason"] or metrics["failure_reason"],
            })
        
        summary[mode] = trajectories
    
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Extract trajectory summary from SUMO results"
    )
    parser.add_argument(
        "--run-id",
        help="Specific run ID (default: latest from latest.json)",
    )
    parser.add_argument(
        "--mode",
        choices=["B00", "B0", "B2"],
        help="Filter by mode (default: all)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=HTML_OUTPUT_DIR / "trajectory_summary.json",
        help="Output JSON file path",
    )
    
    args = parser.parse_args()
    
    # Determine run ID
    if args.run_id:
        run_id = args.run_id
    else:
        run_id = load_latest_run_id(PARAMETER_INPUT_SIM_LATEST)
        print(f"Using latest run: {run_id}")
    
    # Load results
    results_csv = PARAMETER_INPUT_SIM_DIR / run_id / "experiment_results.csv"
    if not results_csv.exists():
        raise FileNotFoundError(f"Results CSV not found: {results_csv}")
    
    # Extract summary
    summary = extract_trajectory_summary(results_csv, args.mode)
    
    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "run_id": run_id,
                "output": str(args.output.relative_to(Path.cwd())),
                "trajectories": summary,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    
    print(f"Wrote trajectory summary to {args.output}")
    for mode, trajs in summary.items():
        print(f"  {mode}: {len(trajs)} trajectories")


if __name__ == "__main__":
    main()
