"""Configuration and constants for visualization."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Result paths
RESULTS_DIR = PROJECT_ROOT / "results"
METRICS_DIR = RESULTS_DIR / "metrics"
PARAMETER_INPUT_SIM_DIR = METRICS_DIR / "parameter_input_sim"
PARAMETER_INPUT_SIM_BO_DIR = METRICS_DIR / "parameter_input_sim_bo"
RUNS_DIR = PROJECT_ROOT / "runs/final"

# Output paths
HTML_OUTPUT_DIR = RESULTS_DIR / "html"
FIGURES_OUTPUT_DIR = RESULTS_DIR / "figures"

# Result file pointers
PARAMETER_INPUT_SIM_LATEST = PARAMETER_INPUT_SIM_DIR / "latest.json"
PARAMETER_INPUT_SIM_BO_LATEST = PARAMETER_INPUT_SIM_BO_DIR / "latest.json"

# Route constants
SEOUL_STATION_ROUTE_ID = "FIRE_TO_SEOUL_STATION"
SEOUL_STATION_START_EDGE = "-381802881#2"
SEOUL_STATION_TARGET_EDGE = "619147738#0"
SEOUL_STATION_ROUTE_LENGTH_M = 2990.17

# Mode colors
MODE_COLORS = {
    "B00": "#a3a3a3",  # gray (free flow baseline)
    "B0": "#dc2626",   # red (baseline with signals)
    "B2": "#2563eb",   # blue (priority control)
}

# Map defaults
MAP_TILES = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_ATTRIBUTION = "&copy; OpenStreetMap contributors"
MAP_DEFAULT_CENTER = [37.556, 126.98]
MAP_DEFAULT_ZOOM = 14

# CSV field constants
EXPERIMENT_RESULT_FIELDS = {
    "mode": "str",
    "parameter_id": "str",
    "repeat_id": "str",
    "emergency_travel_time_sec": "float",
    "emergency_arrived": "bool",
    "emergency_teleport": "bool",
    "final_status": "str",
}

# BO constants
BO_PARAM_FIELDS = ["D_det", "alpha", "G_ext", "T_change_sec"]
BO_SCORE_FIELDS = ["A_delay_sec", "N_delay_sec", "T_recovery_sec", "score_sec"]
