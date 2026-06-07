"""Parse SUMO FCD (Floating Car Data) output for the trajectory animation.

FCD is the only SUMO output carrying per-timestep, per-vehicle position+speed
(see ``04_visualize/FCD_DATA_SPEC.md``). This module streams the (potentially
large) XML with ``iterparse`` and splits the emergency vehicle from background
traffic.

Assumptions (documented in the contract):
- Run with ``--fcd-output.geo true`` so ``x`` holds longitude and ``y`` latitude.
  If explicit ``lon``/``lat`` attributes are present they take precedence.
- Emergency vehicle id starts with ``emergency_`` and embeds the mode token
  (``B0`` / ``B00`` / ``B2``).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .trajectory_parser import EmergencyTrajectory, TrajectoryPoint

EMERGENCY_PREFIX = "emergency_"
_MODE_TOKENS = ("B00", "B2", "B0")  # check B00 before B0 (prefix overlap)


@dataclass
class FcdResult:
    """Parsed FCD: one emergency trajectory + per-timestep background snapshots."""

    emergency: EmergencyTrajectory
    emergency_id: str
    mode: str
    background: list[dict[str, Any]] = field(default_factory=list)  # [{time, vehicles:[...]}]

    @property
    def start_time(self) -> float:
        return self.emergency.start_time

    @property
    def background_vehicle_count(self) -> int:
        return sum(len(snap["vehicles"]) for snap in self.background)


def lane_to_edge(lane_id: str) -> str:
    """Strip the trailing ``_<laneIndex>`` from a SUMO lane id to get the edge id.

    Internal junction lanes (starting with ``:``) are returned unchanged.
    """
    if not lane_id or lane_id.startswith(":"):
        return lane_id
    sep = lane_id.rfind("_")
    return lane_id[:sep] if sep > 0 else lane_id


def infer_mode(emergency_id: str) -> str:
    """Pull the mode token (B00/B0/B2) out of the emergency vehicle id."""
    tokens = emergency_id.split("_")
    for mode in _MODE_TOKENS:
        if mode in tokens:
            return mode
    return ""


def _read_lonlat(elem: ET.Element, geo: bool) -> tuple[float, float] | None:
    """Return (lat, lon) from a <vehicle> element, or None if unparseable."""
    lat = elem.get("lat")
    lon = elem.get("lon")
    if lat is None or lon is None:
        # geo mode: x=lon, y=lat. Non-geo would be projected metres (not usable on a map).
        lon = elem.get("x")
        lat = elem.get("y")
    if lat is None or lon is None:
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _float(value: str | None, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def parse_fcd(
    path: Path,
    *,
    emergency_prefix: str = EMERGENCY_PREFIX,
    geo: bool = True,
    keep_background: bool = True,
    mode: str | None = None,
    parameter_id: str = "",
    repeat_id: str = "",
) -> FcdResult:
    """Stream-parse an FCD XML file.

    Args:
        path: Path to ``fcd.xml``.
        emergency_prefix: Vehicle id prefix identifying the emergency vehicle.
        geo: True if the file was produced with ``--fcd-output.geo`` (x=lon, y=lat).
        keep_background: Keep per-timestep background vehicle snapshots.
        mode: Override mode label; inferred from the emergency id when None.
        parameter_id / repeat_id: Optional metadata stored on the trajectory.

    Returns:
        FcdResult with the emergency trajectory and background snapshots.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"FCD file not found: {path}")

    emergency_id = ""
    resolved_mode = mode or ""
    traj: EmergencyTrajectory | None = None
    background: list[dict[str, Any]] = []

    for _event, elem in ET.iterparse(path, events=("end",)):
        if elem.tag != "timestep":
            continue
        t = _float(elem.get("time"))
        bg_vehicles: list[dict[str, Any]] = []

        for veh in elem.findall("vehicle"):
            vid = veh.get("id", "")
            coords = _read_lonlat(veh, geo)
            if coords is None:
                continue
            lat, lon = coords
            speed_kmh = _float(veh.get("speed")) * 3.6

            if vid.startswith(emergency_prefix):
                if traj is None:
                    emergency_id = vid
                    resolved_mode = mode or infer_mode(vid)
                    traj = EmergencyTrajectory(resolved_mode, parameter_id, repeat_id)
                traj.add_point(
                    TrajectoryPoint(
                        time=t,
                        edge_id=lane_to_edge(veh.get("lane", "")),
                        lat=lat,
                        lon=lon,
                        speed_kmh=speed_kmh,
                        angle=_float(veh.get("angle")),
                        dist_m=_float(veh.get("distance")),
                        lane_id=veh.get("lane", ""),
                        lane_pos_m=_float(veh.get("pos")),
                    )
                )
            elif keep_background:
                bg_vehicles.append({
                    "id": vid,
                    "lat": lat,
                    "lon": lon,
                    "speed_kmh": round(speed_kmh, 2),
                    "angle": round(_float(veh.get("angle")), 1),
                })

        if keep_background and bg_vehicles:
            background.append({"time": t, "vehicles": bg_vehicles})
        elem.clear()  # free parsed timestep to keep memory flat

    if traj is None:
        raise ValueError(
            f"No emergency vehicle (prefix {emergency_prefix!r}) found in {path}"
        )

    return FcdResult(
        emergency=traj,
        emergency_id=emergency_id,
        mode=resolved_mode,
        background=background,
    )
