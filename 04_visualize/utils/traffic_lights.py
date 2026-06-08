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
# All TLS in the network (93). The geometric buffer over this set also catches
# off-corridor signals, so the animation prefers the on-route subset below
# (authoritative: the TLS junctions the route actually passes through, from
# tools/export_route_tls.py).
DEFAULT_TLS_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_tls.geojson"
DEFAULT_ROUTE_TLS_GEOJSON = PROJECT_ROOT / "data_prepared/geojson/ellipse_passenger_route_tls.geojson"

# Influence-zone / speed thresholds for the motion-based approximation (Q2=B).
ROUTE_BUFFER_M = 60.0      # keep TLS whose nearest route point is within this
APPROACH_M = 45.0          # zone starts this far *before* the light (vehicle side)
EXIT_M = 15.0              # zone ends this far *after* the light
STOP_SPEED_KMH = 5.0       # below this inside the zone => red (blocked), else green

# State -> colour is resolved in the HTML; these are the canonical labels.
STATE_OFF = "off"
STATE_RED = "red"
STATE_GREEN = "green"
STATE_YELLOW = "yellow"


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


# --- Real B4 control history (signal_events.csv) -------------------------------
# action_type -> what it does to the EV-facing signal. The B4 runtime logs an
# evaluation/control event row per decision; only a subset actually changes the
# light. We translate those into a green/off timeline anchored on the light.
CONTROL_GREEN_ACTIONS = {
    "phase_change_target_green",   # switch the EV approach to green
    "extend_target_green",         # hold the green longer for the EV
    "return_to_target_green",      # come back to the EV green after a flush
    "entry_hold_release",          # release the fire-station entry hold -> go
}
CONTROL_END_ACTIONS = {
    "restore_previous_phase",      # control window done, hand back to baseline
    "entry_hold",                  # holding (red) the entry until safe
}
# Flush actions briefly serve a cross/blocking movement; we treat them as a
# short non-EV (red-ish) blip but keep them "controlled" so the icon stays lit.
CONTROL_FLUSH_ACTIONS = {
    "downstream_flush_same_tls",
    "same_lane_blocker_flush",
}
CONTROL_ACTIONS = CONTROL_GREEN_ACTIONS | CONTROL_END_ACTIONS | CONTROL_FLUSH_ACTIONS


def control_state_timeline(events: list[dict[str, Any]], anchor: float) -> list[list[Any]]:
    """Build a [t_rel, state] timeline for ONE light from its real control events.

    ``events`` are the signal_events.csv rows for a single tls_id (any order),
    each a dict with ``time`` and ``action_type``. ``anchor`` is the emergency
    departure time, so ``t_rel = time - anchor`` matches the animation clock.

    State semantics:
      * ``green`` while the B4 runtime is holding/extending the EV phase,
      * ``red``   during a flush of a conflicting movement (still controlled),
      * ``off``   before the first control event and after it is restored.
    """
    rows = sorted(
        ((float(e["time"]) - anchor, e.get("action_type", "")) for e in events),
        key=lambda r: r[0],
    )
    timeline: list[list[Any]] = []
    last = None
    for t_rel, action in rows:
        if action in CONTROL_GREEN_ACTIONS:
            state = STATE_GREEN
        elif action in CONTROL_FLUSH_ACTIONS:
            state = STATE_RED
        elif action in CONTROL_END_ACTIONS:
            # entry_hold is a red hold; restore_previous_phase ends control.
            state = STATE_RED if action == "entry_hold" else STATE_OFF
        else:
            continue
        if state != last:
            timeline.append([round(t_rel, 2), state])
            last = state
    if not timeline:
        return [[0.0, STATE_OFF]]
    return timeline


def tls_dump_timeline(events: list[dict[str, Any]], anchor: float) -> list[list[Any]]:
    """Build a [t_rel, state] timeline from a real per-step TLS state dump.

    ``events`` are tls_states.csv rows for ONE light: dicts with ``time`` and a
    pre-resolved ``state`` (green/red/yellow = the EV-facing signal colour SUMO
    actually showed). We just re-anchor the time to the EV clock; the dump is
    already change-compressed so no extra de-duplication is needed.
    """
    rows = sorted(
        ((float(e["time"]) - anchor, e.get("state", STATE_OFF)) for e in events),
        key=lambda r: r[0],
    )
    timeline = [[round(t, 2), s] for t, s in rows]
    if not timeline or timeline[0][0] > 0:
        # show the first known colour from t=0 so the icon is never blank at start
        first = timeline[0][1] if timeline else STATE_OFF
        timeline.insert(0, [0.0, first])
    return timeline


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


def _augment_from_tls_dump(
    doc: dict[str, Any],
    per_mode_history: dict[str, dict[str, dict[str, Any]]],
    modes: list[str],
    *,
    route_buffer_m: float = ROUTE_BUFFER_M,
) -> dict[str, Any]:
    """Inject ``traffic_lights`` + per-mode ``tls_states`` straight from the dump.

    One icon per recorded movement ``(tls_id, link_index)``, placed at its own
    stop-line coords, replaying its real colour timeline for the WHOLE run. This
    bypasses the geojson + position-match + motion-proxy path entirely, so every
    on-route signal the simulation recorded stays active the whole animation
    (no proximity gating, no match-gap lights stuck "off"). A movement present in
    one mode's dump but missing from another shows ``off`` in the latter.
    """
    # Union of dumped movements across modes, keyed by the dump key (tls_id#link).
    positions: dict[str, tuple[float, float]] = {}
    for m in modes:
        for key, info in per_mode_history.get(m, {}).items():
            if info.get("kind") != "tls_dump" or info.get("lat") is None:
                continue
            positions.setdefault(key, (info["lat"], info["lon"]))

    kept: list[dict[str, Any]] = []
    states: dict[str, dict[str, list[list[Any]]]] = {m: {} for m in modes}
    for key, (lat, lon) in positions.items():
        s_m: dict[str, float | None] = {}
        for m in modes:
            emergency = doc["modes"][m].get("emergency", [])
            if emergency:
                d_min, s = project_to_route({"lat": lat, "lon": lon}, emergency)
                s_m[m] = round(s, 2) if d_min <= route_buffer_m else None
            else:
                s_m[m] = None
            info = per_mode_history.get(m, {}).get(key)
            if info is not None and info.get("kind") == "tls_dump":
                anchor = doc["modes"][m].get("depart_time_sec", 0.0)
                states[m][key] = tls_dump_timeline(info["events"], anchor)
            else:
                states[m][key] = [[0.0, STATE_OFF]]
        kept.append({
            "tls_id": key,
            "lat": round(float(lat), 6),
            "lon": round(float(lon), 6),
            "phase_count": None,
            "s_m": s_m,
            "controlled": True,
        })

    doc["traffic_lights"] = kept
    for m in modes:
        doc["modes"][m]["tls_states"] = states[m]
    doc.setdefault("meta", {})["tls_approx"] = {
        "method": "real_tls_dump_direct",
        "control_matched": {m: len(per_mode_history.get(m, {})) for m in modes},
        "route_buffer_m": route_buffer_m,
    }
    return {
        "tls_total": len(positions),
        "tls_kept": len(kept),
        "per_mode": {m: len(states[m]) for m in modes},
        "control_used": {m: len(per_mode_history.get(m, {})) for m in modes},
        "control_matched": {m: len(per_mode_history.get(m, {})) for m in modes},
    }


def load_static_tls_program(net_file: Path, tls_id: str) -> dict[str, Any] | None:
    """Read a *static* tlLogic from the SUMO net: phases, cycle, offset.

    Returns ``None`` if the TLS has no tlLogic or no phases. Used to render an
    on-screen signal the per-step dump did not record (e.g. a cross junction the
    EV never traverses): a static program is deterministic, so its colour over
    time is computable from the net alone (no simulation re-run).
    """
    import xml.etree.ElementTree as ET

    root = ET.parse(str(net_file)).getroot()
    tl = next((t for t in root.findall("tlLogic") if t.get("id") == tls_id), None)
    if tl is None:
        return None
    phases = [(int(p.get("duration", "0")), p.get("state", "")) for p in tl.findall("phase")]
    phases = [(d, s) for d, s in phases if s]
    if not phases:
        return None
    return {
        "phases": phases,
        "cycle": sum(d for d, _ in phases),
        "offset": int(float(tl.get("offset") or 0)),
        "type": tl.get("type", ""),
        "program_id": tl.get("programID", ""),
    }


def static_tls_position(net_file: Path, tls_id: str, link_index: int | None = None) -> tuple[float, float] | None:
    """(lat, lon) of a TLS from the net: a link's stop line, else the centroid."""
    import sumolib

    net = sumolib.net.readNet(str(net_file))
    try:
        tls = net.getTLS(tls_id)
    except KeyError:
        return None
    conns = tls.getConnections()
    if not conns:
        return None
    if link_index is not None:
        sel = [c for c in conns if str(c[2]) == str(link_index)]
        if sel:
            x, y = sel[0][0].getShape()[-1]
            lon, lat = net.convertXY2LonLat(x, y)
            return round(lat, 6), round(lon, 6)
    xs = [c[0].getShape()[-1][0] for c in conns]
    ys = [c[0].getShape()[-1][1] for c in conns]
    lon, lat = net.convertXY2LonLat(sum(xs) / len(xs), sum(ys) / len(ys))
    return round(lat, 6), round(lon, 6)


def static_state_timeline(
    program: dict[str, Any], link_index: int, anchor: float, t_max_rel: float, step: float = 1.0
) -> list[list[Any]]:
    """Compressed ``[[t_rel, state], ...]`` for one link of a static program.

    SUMO static phase at absolute time ``t`` uses ``(t - offset) % cycle``
    (validated against the real dump). ``anchor`` is the EV depart time so the
    timeline shares the animation clock; states are sampled to ``t_max_rel``.
    """
    phases, cycle, offset = program["phases"], program["cycle"], program["offset"]

    def char_at(t: float) -> str:
        tt = (t - offset) % cycle
        acc = 0
        for dur, state in phases:
            if tt < acc + dur:
                return state[link_index] if link_index < len(state) else "r"
            acc += dur
        return phases[-1][1][link_index] if link_index < len(phases[-1][1]) else "r"

    def norm(ch: str) -> str:
        return STATE_GREEN if ch in "Gg" else (STATE_YELLOW if ch in "yY" else STATE_RED)

    timeline: list[list[Any]] = []
    last = None
    t = 0.0
    while t <= t_max_rel + 1:
        s = norm(char_at(anchor + t))
        if s != last:
            timeline.append([round(t, 2), s])
            last = s
        t += step
    return timeline or [[0.0, STATE_OFF]]


def add_static_tls_to_doc(
    doc: dict[str, Any], net_file: Path, tls_id: str, link_index: int = 0
) -> dict[str, Any]:
    """Inject a static (dump-less) TLS into ``doc`` so it renders like the rest.

    Computes the signal's colour timeline per mode from the net's static program
    and appends a ``traffic_lights`` icon + per-mode ``tls_states`` entry. Returns
    a small summary; raises B4 nothing (caller validates inputs).
    """
    program = load_static_tls_program(net_file, tls_id)
    if program is None:
        return {"ok": False, "reason": "no_static_tllogic"}
    if program["type"] not in ("static", "", None):
        # actuated/delay-based programs are traffic-dependent; the net cannot
        # reproduce the simulated colours, so we refuse rather than fake them.
        return {"ok": False, "reason": f"non_static_type:{program['type']}"}
    pos = static_tls_position(net_file, tls_id, link_index)
    if pos is None:
        return {"ok": False, "reason": "no_position"}
    lat, lon = pos
    modes = list(doc.get("modes", {}).keys())
    for m in modes:
        anchor = doc["modes"][m].get("depart_time_sec", 0.0)
        t_max = doc["modes"][m].get("travel_time_sec", 0.0)
        doc["modes"][m].setdefault("tls_states", {})[tls_id] = static_state_timeline(
            program, link_index, anchor, t_max
        )
    doc.setdefault("traffic_lights", []).append({
        "tls_id": tls_id,
        "lat": lat,
        "lon": lon,
        "phase_count": len(program["phases"]),
        "s_m": {m: None for m in modes},
        "controlled": False,
        "source": "net_static_program",
    })
    return {"ok": True, "tls_id": tls_id, "link_index": link_index,
            "cycle": program["cycle"], "offset": program["offset"]}


def augment_doc_with_tls(
    doc: dict[str, Any],
    geojson_path: Path = DEFAULT_TLS_GEOJSON,
    *,
    route_buffer_m: float = ROUTE_BUFFER_M,
    approach_m: float = APPROACH_M,
    exit_m: float = EXIT_M,
    stop_speed_kmh: float = STOP_SPEED_KMH,
    control_history: dict[str, dict[str, Any]] | None = None,
    control_modes: tuple[str, ...] = (),
    control_match_m: float = 80.0,
) -> dict[str, Any]:
    """Inject ``traffic_lights`` (positions) and per-mode ``tls_states`` into doc.

    ``doc`` must already contain ``modes[*].emergency`` (with ``dist_m`` and
    ``speed_kmh``). Lights farther than ``route_buffer_m`` from every mode's route
    are dropped. Returns a small summary for logging.

    If ``control_history`` is given, the listed ``control_modes`` use the REAL B4
    control timeline (from signal_events.csv) instead of the motion proxy. The
    history is keyed by the runtime tls_id and carries its own ``lat``/``lon`` so
    it can be matched to the on-route geojson lights by nearest position (the two
    id namespaces differ: ``joinedS_…`` runtime TLS vs ``cluster_…`` geojson
    junctions). Lights without a control match fall back to the proxy.
    """
    points = load_tls_points(geojson_path)
    modes = list(doc.get("modes", {}).keys())
    control_history = control_history or {}

    # Normalise to per-mode history: {mode: {runtime_tls_id: info}}.
    # Back-compat: a flat {tls_id: info} + control_modes applies to those modes.
    if control_history and not all(isinstance(v, dict) and "events" not in v for v in control_history.values()):
        per_mode_history = {m: control_history for m in control_modes}
    else:
        per_mode_history = control_history  # already {mode: {...}}

    # When a real per-step dump is present, drive icons straight from it so every
    # on-route signal replays its real colours for the whole run (see
    # _augment_from_tls_dump). The geojson/proxy path below is only for the
    # motion-proxy and B4-control-event fallbacks.
    dump_present = any(
        info.get("kind") == "tls_dump"
        for hist in per_mode_history.values()
        for info in hist.values()
    )
    if dump_present:
        return _augment_from_tls_dump(doc, per_mode_history, modes, route_buffer_m=route_buffer_m)

    def match_to_geo(history: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Position-match each runtime TLS to its nearest on-route geojson light."""
        by_geo: dict[str, dict[str, Any]] = {}
        for rt_id, info in history.items():
            clat, clon = info.get("lat"), info.get("lon")
            if clat is None or clon is None:
                continue
            best_id, best_d = None, math.inf
            for tls in points:
                d = meters_between(clat, clon, tls["lat"], tls["lon"])
                if d < best_d:
                    best_d, best_id = d, tls["tls_id"]
            if best_id is not None and best_d <= control_match_m:
                prev = by_geo.get(best_id)
                if prev is None or best_d < prev["dist_m"]:
                    by_geo[best_id] = {
                        "events": info.get("events", []),
                        "kind": info.get("kind", "control_events"),
                        "runtime_tls_id": rt_id,
                        "dist_m": round(best_d, 1),
                    }
        return by_geo

    # geojson_tls_id -> matched control info, per mode.
    control_by_geo: dict[str, dict[str, dict[str, Any]]] = {
        m: match_to_geo(per_mode_history.get(m, {})) for m in modes
    }

    # In real-dump mode, lights with no dump match are NOT signal controllers in
    # the sim (node-only 'traffic_light' artifacts or junctions merged into an
    # adjacent TLS). Show them as off rather than the motion proxy, which would
    # reintroduce the EV-speed-shadow flicker the dump exists to remove.
    dump_mode_per: dict[str, bool] = {
        m: any(info.get("kind") == "tls_dump" for info in control_by_geo[m].values())
        for m in modes
    }

    kept: list[dict[str, Any]] = []
    states: dict[str, dict[str, list[list[Any]]]] = {m: {} for m in modes}
    control_used: dict[str, int] = {m: 0 for m in modes}

    for tls in points:
        s_m: dict[str, float | None] = {}
        near_any = False
        any_controlled = False
        for m in modes:
            emergency = doc["modes"][m].get("emergency", [])
            if not emergency:
                s_m[m] = None
                continue
            d_min, s = project_to_route(tls, emergency)
            if d_min <= route_buffer_m:
                s_m[m] = round(s, 2)
                near_any = True
                ctl = control_by_geo[m].get(tls["tls_id"])
                if ctl is not None:
                    any_controlled = True
                    anchor = doc["modes"][m].get("depart_time_sec", 0.0)
                    if ctl.get("kind") == "tls_dump":
                        states[m][tls["tls_id"]] = tls_dump_timeline(ctl["events"], anchor)
                    else:
                        states[m][tls["tls_id"]] = control_state_timeline(ctl["events"], anchor)
                    control_used[m] += 1
                elif dump_mode_per[m]:
                    states[m][tls["tls_id"]] = [[0.0, STATE_OFF]]
                else:
                    states[m][tls["tls_id"]] = approximate_state_timeline(
                        emergency, s,
                        approach_m=approach_m, exit_m=exit_m, stop_speed_kmh=stop_speed_kmh,
                    )
            else:
                s_m[m] = None
        if near_any:
            kept.append({**tls, "s_m": s_m, "controlled": any_controlled})

    doc["traffic_lights"] = kept
    for m in modes:
        doc["modes"][m]["tls_states"] = states[m]
    has_dump = any(
        info.get("kind") == "tls_dump"
        for mode_map in control_by_geo.values() for info in mode_map.values()
    )
    doc.setdefault("meta", {})["tls_approx"] = {
        "method": "real_tls_dump" if has_dump else ("b4_control_history" if control_history else "motion_speed_proxy"),
        "control_matched": {m: len(control_by_geo[m]) for m in modes},
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
        "control_used": control_used,
        "control_matched": {m: len(control_by_geo[m]) for m in modes},
    }
