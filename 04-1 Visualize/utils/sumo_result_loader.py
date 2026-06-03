"""Load and parse SUMO simulation results from CSV files."""

import csv
from pathlib import Path
from typing import Any


def load_experiment_results_csv(csv_path: Path) -> list[dict[str, Any]]:
    """
    Load experiment results CSV.
    
    Args:
        csv_path: Path to experiment_results.csv
        
    Returns:
        List of result rows
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Results CSV not found: {csv_path}")
    
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    return rows


def filter_results_by_mode(rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """Filter results by mode (B00, B0, B2)."""
    return [row for row in rows if row.get("mode") == mode]


def parse_float_field(value: Any, default: float = 0.0) -> float:
    """Safely parse float field from CSV."""
    if value in {"", None}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_bool_field(value: Any, default: bool = False) -> bool:
    """Safely parse boolean field from CSV."""
    if isinstance(value, bool):
        return value
    if value in {"", None}:
        return default
    if str(value).lower() in {"true", "1", "yes"}:
        return True
    if str(value).lower() in {"false", "0", "no"}:
        return False
    return default


def extract_emergency_metrics(row: dict[str, Any]) -> dict[str, Any]:
    """Extract emergency vehicle metrics from a result row."""
    return {
        "mode": row.get("mode", ""),
        "parameter_id": row.get("parameter_id", ""),
        "repeat_id": row.get("repeat_id", ""),
        "travel_time_sec": parse_float_field(row.get("emergency_travel_time_sec")),
        "arrived": parse_bool_field(row.get("emergency_arrived")),
        "teleported": parse_bool_field(row.get("emergency_teleport")),
        "final_status": row.get("final_status", ""),
        "avg_speed_kmh": parse_float_field(row.get("emergency_avg_speed_kmh")),
        "warning_reason": row.get("warning_reason", ""),
        "failure_reason": row.get("failure_reason", ""),
    }
