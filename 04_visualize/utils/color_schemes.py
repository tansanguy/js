"""Color schemes for visualization."""

from typing import Literal

# Mode colors
MODE_COLORS = {
    "B00": "#a3a3a3",  # gray (free flow baseline)
    "B0": "#dc2626",   # red (baseline with signals)
    "B2": "#2563eb",   # blue (priority control)
}

# Speed-based colors (for heatmap)
SPEED_COLORS = {
    "very_slow": "#7c2d12",    # dark orange (0-10 km/h)
    "slow": "#dc2626",          # red (10-20 km/h)
    "moderate": "#f59e0b",      # amber (20-30 km/h)
    "fast": "#10b981",          # green (30-40 km/h)
    "very_fast": "#2563eb",     # blue (40+ km/h)
}

# Status colors
STATUS_COLORS = {
    "PASS": "#10b981",           # green
    "FAIL": "#dc2626",           # red
    "WARNING": "#f59e0b",        # amber
    "UNKNOWN": "#6b7280",        # gray
}


def get_mode_color(mode: str) -> str:
    """Get color for mode."""
    return MODE_COLORS.get(mode, "#6b7280")


def get_speed_color(speed_kmh: float) -> str:
    """Get color based on speed."""
    if speed_kmh < 10:
        return SPEED_COLORS["very_slow"]
    elif speed_kmh < 20:
        return SPEED_COLORS["slow"]
    elif speed_kmh < 30:
        return SPEED_COLORS["moderate"]
    elif speed_kmh < 40:
        return SPEED_COLORS["fast"]
    else:
        return SPEED_COLORS["very_fast"]


def get_status_color(status: str) -> str:
    """Get color based on status."""
    return STATUS_COLORS.get(status.upper(), STATUS_COLORS["UNKNOWN"])
