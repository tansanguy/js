"""Map SUMO traffic lights onto the emergency-progress animation.

The animation shows the signal state through *traffic-light icons placed at the
real TLS positions* (instead of recolouring the emergency vehicle itself). The
authoritative signal-colour time series is not collected yet (see
``FCD_DATA_SPEC.md``); until a re-run records ``getRedYellowGreenState`` every
step (the "C" upgrade), we approximate each light's state from the emergency
vehicle's own motion as it passes:

* a light is ``"off"`` (idle / unknown) until the emergency vehicle enters its
  influence zone along the route,
* inside the zone it is ``"green"`` when the vehicle is moving and ``"red"``
  when the vehicle is stopped (blocked),
* once the vehicle has cleared the light it returns to ``"off"``.

So B0 (no priority) shows lights going red as the vehicle stalls at them, while
B2 (corridor priority) shows them green as the vehicle rolls through — the same
"signal flow" story the EV-colour trick used to carry, now on the lights.

The TLS positions come from ``ellipse_passenger_tls.geojson`` and are matched to
the route by nearest-point projection, so swapping in real per-step colours
later only means replacing :func:`approximate_state_timeline` — the document
shape (``traffic_lights`` + per-mode ``tls_states``) stays identical.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TLS_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_tls.geojson"

# Influence-zone / speed thresholds for the motion-based approximation (Q2=B).
ROUTE_BUFFER_M = 60.0      # keep TLS whose nearest route point is within this
APPROACH_M = 45.0          # zone starts this far *before* the light (vehicle side)
EXIT_M = 15.0              # zone ends this far *after* the light
STOP_SPEED_KMH = 5.0       # below this inside the zone => red (blocked), else green

# State -> colour is resolved in the HTML; these are the canonical labels.
STATE_OFF = "off"
STATE_RED = "red"
STATE_GREEN = "green"


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Flat-earth metre distance (accurate for the sub-km separations here)."""
    dlat = (lat2 - lat1) * 111320.0
    dlon = (lon2 - lon1) * 111320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlon)


def load_tls_points(geojson_path: Path) -> list[dict[str, Any]]:
    """Load TLS positions from the network TLS geojson.

    Returns ``[{"tls_id", "lat", "lon", "phase_count"}]`` de-duplicated by tls_id.
    """
    data = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        tls_id = props.get("tls_id") or props.get("junction_id") or props.get("node_id")
        if not tls_id or tls_id in out:
            continue
        geom = feat.get("geometry", {}).get("coordinates") or [None, None]
        lat = props.get("lat", geom[1])
        lon = props.get("lon", geom[0])
        if lat is None or lon is None:
            continue
        out[tls_id] = {
            "tls_id": tls_id,
            "lat": round(float(lat), 6),
            "lon": round(float(lon), 6),
            "phase_count": props.get("phase_count"),
        }
    return list(out.values())


def project_to_route(tls: dict[str, Any], emergency: list[dict[str, Any]]) -> tuple[float, float]:
    """Nearest-point projection of a TLS onto an emergency trajectory.

    Returns ``(min_dist_m, route_dist_m)``: the perpendicular-ish distance to the
    closest trajectory sample and that sample's cumulative route distance (the
    light's position *along* the corridor).
    """
    best_d = math.inf
    best_s = 0.0
    for p in emergency:
        d = meters_between(tls["lat"], tls["lon"], p["lat"], p["lon"])
        if d < best_d:
            best_d = d
            best_s = p["dist_m"]
    return best_d, best_s


def approximate_state_timeline(
    emergency: list[dict[str, Any]],
    s_tls: float,
    *,
    approach_m: float = APPROACH_M,
    exit_m: float = EXIT_M,
    stop_speed_kmh: float = STOP_SPEED_KMH,
) -> list[list[Any]]:
    """Motion-based signal-state series for one light, compressed to changes.

    ``s_tls`` is the light's route distance. For each emergency sample we map the
    along-route offset to a state and emit ``[t_rel, state]`` only when it flips.
    Replace this function with real per-step TLS colours for the "C" upgrade.
    """
    timeline: list[list[Any]] = []
    last = None
    for p in emergency:
        delta = s_tls - p["dist_m"]  # >0: light still ahead, <0: vehicle passed it
        if -exit_m <= delta <= approach_m:
            state = STATE_RED if p["speed_kmh"] < stop_speed_kmh else STATE_GREEN
        else:
            state = STATE_OFF
        if state != last:
            timeline.append([p["t_rel"], state])
            last = state
    if not timeline:
        timeline.append([0.0, STATE_OFF])
    return timeline


def augment_doc_with_tls(
    doc: dict[str, Any],
    geojson_path: Path = DEFAULT_TLS_GEOJSON,
    *,
    route_buffer_m: float = ROUTE_BUFFER_M,
    approach_m: float = APPROACH_M,
    exit_m: float = EXIT_M,
    stop_speed_kmh: float = STOP_SPEED_KMH,
) -> dict[str, Any]:
    """Inject ``traffic_lights`` (positions) and per-mode ``tls_states`` into doc.

    ``doc`` must already contain ``modes[*].emergency`` (with ``dist_m`` and
    ``speed_kmh``). Lights farther than ``route_buffer_m`` from every mode's route
    are dropped. Returns a small summary for logging.
    """
    points = load_tls_points(geojson_path)
    modes = list(doc.get("modes", {}).keys())

    kept: list[dict[str, Any]] = []
    states: dict[str, dict[str, list[list[Any]]]] = {m: {} for m in modes}

    for tls in points:
        s_m: dict[str, float | None] = {}
        near_any = False
        for m in modes:
            emergency = doc["modes"][m].get("emergency", [])
            if not emergency:
                s_m[m] = None
                continue
            d_min, s = project_to_route(tls, emergency)
            if d_min <= route_buffer_m:
                s_m[m] = round(s, 2)
                near_any = True
                states[m][tls["tls_id"]] = approximate_state_timeline(
                    emergency, s,
                    approach_m=approach_m, exit_m=exit_m, stop_speed_kmh=stop_speed_kmh,
                )
            else:
                s_m[m] = None
        if near_any:
            kept.append({**tls, "s_m": s_m})

    doc["traffic_lights"] = kept
    for m in modes:
        doc["modes"][m]["tls_states"] = states[m]
    doc.setdefault("meta", {})["tls_approx"] = {
        "method": "motion_speed_proxy",
        "route_buffer_m": route_buffer_m,
        "approach_m": approach_m,
        "exit_m": exit_m,
        "stop_speed_kmh": stop_speed_kmh,
        "source": str(Path(geojson_path).name),
    }
    return {
        "tls_total": len(points),
        "tls_kept": len(kept),
        "per_mode": {m: len(states[m]) for m in modes},
    }
