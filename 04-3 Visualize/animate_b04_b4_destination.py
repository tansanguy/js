#!/usr/bin/env python3
"""Build a B04/B4 destination-arrival animation from SUMO FCD output."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIZ_04_1_DIR = PROJECT_ROOT / "04-1 Visualize"
if str(VIZ_04_1_DIR) not in sys.path:
    sys.path.insert(0, str(VIZ_04_1_DIR))

from utils.fcd_parser import FcdResult, lane_to_edge, parse_fcd  # noqa: E402


RUN_ROOT = PROJECT_ROOT / "runs/compact_v9_B4"
HTML_OUTPUT_DIR = PROJECT_ROOT / "results/html"
DEFAULT_RUN_ID = "b4_thold_seed1_fcd_viz"
DEFAULT_OUTPUT_JSON = HTML_OUTPUT_DIR / "compact_v9_b04_b4_destination_animation.json"
DEFAULT_OUTPUT_HTML = HTML_OUTPUT_DIR / "compact_v9_b04_b4_destination_animation.html"
EV_ID = "emergency_0"
TARGET_LABEL = "Seoul Station Front"
MAP_TILES = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
MAP_ATTRIBUTION = "&copy; OpenStreetMap contributors"
MODE_COLORS = {"B04": "#dc2626", "B4": "#2563eb"}


class B04B4AnimationError(RuntimeError):
    """Expected visualization failure."""


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {"", None}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def bool_from_tripinfo(value: Any) -> bool:
    return value not in {"", None, "-1"}


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * 111_320.0
    dlon = (lon2 - lon1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dlat, dlon)


def parse_tripinfo(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise B04B4AnimationError(f"missing_tripinfo:{rel(path)}")
    try:
        for _event, elem in ET.iterparse(path, events=("end",)):
            if elem.tag == "tripinfo" and elem.get("id") == EV_ID:
                return dict(elem.attrib)
            elem.clear()
    except ET.ParseError:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped.startswith("<tripinfo ") or not stripped.endswith("/>"):
                continue
            try:
                elem = ET.fromstring(stripped)
            except ET.ParseError:
                continue
            if elem.get("id") == EV_ID:
                return dict(elem.attrib)
    raise B04B4AnimationError(f"missing_emergency_tripinfo:{rel(path)}")


def tripinfo_or_fcd_fallback(path: Path, fcd: FcdResult, mode: str) -> dict[str, str]:
    try:
        return parse_tripinfo(path)
    except B04B4AnimationError as exc:
        if not str(exc).startswith("missing_emergency_tripinfo:"):
            raise
        points = fcd.emergency.points
        if not points:
            raise
        duration = max(0.0, points[-1].time - fcd.emergency.start_time)
        return {
            "id": EV_ID,
            "depart": str(fcd.emergency.start_time),
            "arrival": "-1",
            "arrivalLane": points[-1].edge_id,
            "arrivalPos": "",
            "arrivalSpeed": "0",
            "duration": str(duration),
            "waitingTime": "0",
            "waitingCount": "0",
            "timeLoss": "0",
            "fallback_reason": f"{mode}_missing_emergency_tripinfo_partial_fcd",
        }


def emergency_pos_by_time(fcd: FcdResult) -> dict[float, tuple[float, float]]:
    return {point.time: (point.lat, point.lon) for point in fcd.emergency.points}


def interp_pos(fcd: FcdResult, t_abs: float) -> tuple[float, float]:
    points = fcd.emergency.points
    if not points:
        return (0.0, 0.0)
    if t_abs <= points[0].time:
        return (points[0].lat, points[0].lon)
    if t_abs >= points[-1].time:
        return (points[-1].lat, points[-1].lon)
    for index in range(1, len(points)):
        prev_point = points[index - 1]
        next_point = points[index]
        if prev_point.time <= t_abs <= next_point.time:
            span = next_point.time - prev_point.time
            ratio = 0.0 if span == 0 else (t_abs - prev_point.time) / span
            return (
                prev_point.lat + (next_point.lat - prev_point.lat) * ratio,
                prev_point.lon + (next_point.lon - prev_point.lon) * ratio,
            )
    return (points[-1].lat, points[-1].lon)


def build_mode_payload(
    *,
    mode: str,
    fcd: FcdResult,
    tripinfo: dict[str, str],
    bg_radius_m: float,
) -> dict[str, Any]:
    points = fcd.emergency.points
    if not points:
        raise B04B4AnimationError(f"empty_emergency_fcd:{mode}")

    anchor = fcd.emergency.start_time
    cumulative: list[float] = []
    total = 0.0
    previous = None
    for point in points:
        if previous is not None:
            total += meters_between(previous.lat, previous.lon, point.lat, point.lon)
        cumulative.append(total)
        previous = point

    route_length_m = safe_float(tripinfo.get("routeLength"), total)

    def normalized_distance(distance_m: float) -> float:
        return round(distance_m / total * route_length_m, 2) if total else 0.0

    series = [
        {
            "t_rel": round(point.time - anchor, 2),
            "lat": round(point.lat, 6),
            "lon": round(point.lon, 6),
            "speed_kmh": round(point.speed_kmh, 2),
            "angle": round(point.angle, 1),
            "dist_m": normalized_distance(cumulative[index]),
            "edge": point.edge_id,
        }
        for index, point in enumerate(points)
    ]

    emergency_positions = emergency_pos_by_time(fcd)
    background = []
    for snap in fcd.background:
        ref = emergency_positions.get(safe_float(snap.get("time")))
        if ref is None:
            continue
        elat, elon = ref
        nearby = [
            {
                "lat": round(vehicle["lat"], 6),
                "lon": round(vehicle["lon"], 6),
                "speed_kmh": round(safe_float(vehicle.get("speed_kmh")), 2),
                "angle": round(safe_float(vehicle.get("angle")), 1),
            }
            for vehicle in snap["vehicles"]
            if meters_between(elat, elon, vehicle["lat"], vehicle["lon"]) <= bg_radius_m
        ]
        if nearby:
            background.append({"t_rel": round(safe_float(snap.get("time")) - anchor, 2), "vehicles": nearby})

    arrival_lane = tripinfo.get("arrivalLane", "")
    arrival_edge = lane_to_edge(arrival_lane)
    final_edge = series[-1]["edge"]
    if arrival_edge and final_edge != arrival_edge:
        raise B04B4AnimationError(f"final_edge_mismatch:{mode}:fcd={final_edge}:tripinfo={arrival_edge}")

    travel_time_sec = safe_float(tripinfo.get("duration"), points[-1].time - anchor)
    speeds = [point.speed_kmh for point in points]
    return {
        "mode": mode,
        "emergency_id": fcd.emergency_id,
        "depart_time_sec": anchor,
        "travel_time_sec": round(travel_time_sec, 2),
        "arrival_time_sec": safe_float(tripinfo.get("arrival")),
        "route_length_m": round(route_length_m, 2),
        "avg_speed_kmh": round(route_length_m / travel_time_sec * 3.6, 2) if travel_time_sec else 0.0,
        "max_speed_kmh": round(max(speeds), 2) if speeds else 0.0,
        "waiting_time_sec": safe_float(tripinfo.get("waitingTime")),
        "waiting_count": int(safe_float(tripinfo.get("waitingCount"))),
        "time_loss_sec": safe_float(tripinfo.get("timeLoss")),
        "arrival_edge": arrival_edge,
        "arrival_lane": arrival_lane,
        "arrival_pos": safe_float(tripinfo.get("arrivalPos")),
        "arrival_speed_kmh": round(safe_float(tripinfo.get("arrivalSpeed")) * 3.6, 2),
        "emergency_arrived": bool_from_tripinfo(tripinfo.get("arrival")),
        "emergency_teleport": bool(tripinfo.get("vaporized")),
        "emergency": series,
        "background": background,
        "route_polyline": [[point["lat"], point["lon"]] for point in series],
        "destination": {"lat": series[-1]["lat"], "lon": series[-1]["lon"], "label": TARGET_LABEL},
    }


def load_signal_events(path: Path, fcd: FcdResult) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    anchor = fcd.emergency.start_time
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            if not row.get("tls_id"):
                continue
            t_abs = safe_float(row.get("time"), -1.0)
            if t_abs < anchor:
                continue
            lat, lon = interp_pos(fcd, t_abs)
            events.append({
                "t_rel": round(t_abs - anchor, 2),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "stage": row.get("stage", ""),
                "action_type": row.get("action_type", ""),
                "tls_id": row.get("tls_id", ""),
                "movement_id": row.get("movement_id", ""),
                "trigger_reason": row.get("trigger_reason", ""),
                "target_phase": row.get("target_phase", ""),
                "previous_phase": row.get("previous_phase", ""),
            })
    return sorted(events, key=lambda item: item["t_rel"])


def bounds_for(modes: list[dict[str, Any]]) -> dict[str, float]:
    lats: list[float] = []
    lons: list[float] = []
    for mode in modes:
        for lat, lon in mode["route_polyline"]:
            lats.append(lat)
            lons.append(lon)
    return {
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
        "center_lat": (min(lats) + max(lats)) / 2.0,
        "center_lon": (min(lons) + max(lons)) / 2.0,
    }


def run_paths(run_id: str, run_root: Path) -> dict[str, dict[str, Path]]:
    root = run_root / run_id
    return {
        "B04": {
            "run_dir": root / "B04/no_control/repeat_001",
            "fcd": root / "B04/no_control/repeat_001/fcd.xml",
            "tripinfo": root / "B04/no_control/repeat_001/tripinfo.xml",
        },
        "B4": {
            "run_dir": root / "B4/B4_MVP_DEFAULT/repeat_001",
            "fcd": root / "B4/B4_MVP_DEFAULT/repeat_001/fcd.xml",
            "tripinfo": root / "B4/B4_MVP_DEFAULT/repeat_001/tripinfo.xml",
            "signal_events": root / "B4/B4_MVP_DEFAULT/repeat_001/signal_events.csv",
        },
    }


def build_doc(args: argparse.Namespace) -> dict[str, Any]:
    paths = run_paths(args.run_id, args.run_root)
    for mode in ("B04", "B4"):
        if not paths[mode]["fcd"].is_file():
            raise B04B4AnimationError(f"missing_fcd:{rel(paths[mode]['fcd'])}")

    b04_fcd = parse_fcd(paths["B04"]["fcd"], mode="B04")
    b4_fcd = parse_fcd(paths["B4"]["fcd"], mode="B4")
    b04_tripinfo = tripinfo_or_fcd_fallback(paths["B04"]["tripinfo"], b04_fcd, "B04")
    b4_tripinfo = tripinfo_or_fcd_fallback(paths["B4"]["tripinfo"], b4_fcd, "B4")
    b04_payload = build_mode_payload(mode="B04", fcd=b04_fcd, tripinfo=b04_tripinfo, bg_radius_m=args.bg_radius_m)
    b4_payload = build_mode_payload(mode="B4", fcd=b4_fcd, tripinfo=b4_tripinfo, bg_radius_m=args.bg_radius_m)
    b4_payload["signal_events"] = load_signal_events(paths["B4"]["signal_events"], b4_fcd)

    return {
        "schema": "compact_v9_b04_b4_destination_animation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "meta": {
            "bg_radius_m": args.bg_radius_m,
            "target_edge": b4_payload["arrival_edge"],
            "target_label": TARGET_LABEL,
            "source": {
                "B04": {key: rel(value) for key, value in paths["B04"].items()},
                "B4": {key: rel(value) for key, value in paths["B4"].items()},
            },
            "bounds": bounds_for([b04_payload, b4_payload]),
        },
        "modes": {"B04": b04_payload, "B4": b4_payload},
    }


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  :root{--b04:#dc2626;--b4:#2563eb;--panel:#111827;--line:#263244;}
  html,body{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0b1220;color:#f8fafc;}
  .wrap{display:grid;grid-template-rows:auto 1fr 190px;height:100vh;min-height:680px;}
  header{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:10px 14px;background:#111827;border-bottom:1px solid #263244;}
  h1{font-size:16px;line-height:1.2;margin:0;font-weight:750;}
  .controls{display:flex;align-items:center;gap:10px;flex:1;min-width:320px;}
  button{height:32px;border:0;border-radius:6px;background:#2563eb;color:#fff;padding:0 13px;font-weight:700;cursor:pointer;}
  button.secondary{background:#374151;}
  select{height:30px;border-radius:6px;border:1px solid #475569;background:#111827;color:#fff;}
  input[type=range]{flex:1;min-width:160px;}
  .clock{font-variant-numeric:tabular-nums;min-width:170px;color:#cbd5e1;font-size:13px;}
  .maps{display:grid;grid-template-columns:1fr 1fr;min-height:0;}
  .panel{position:relative;min-width:0;border-right:1px solid #263244;}
  .map{position:absolute;inset:0;background:#1e293b;}
  .tag{position:absolute;z-index:500;left:10px;top:10px;background:rgba(15,23,42,.88);border:1px solid rgba(148,163,184,.28);border-radius:8px;padding:9px 11px;line-height:1.45;font-size:12px;max-width:330px;}
  .tag b{display:block;font-size:14px;margin-bottom:2px;}
  .tag span{font-variant-numeric:tabular-nums;}
  .bottom{display:grid;grid-template-columns:330px 1fr;background:#0f172a;border-top:1px solid #263244;}
  .overview{position:relative;border-right:1px solid #263244;}
  .overview .label{position:absolute;z-index:500;left:8px;top:8px;background:rgba(15,23,42,.82);padding:4px 7px;border-radius:5px;color:#cbd5e1;font-size:12px;}
  .stats{padding:10px 14px;overflow:auto;}
  .stats h2{font-size:13px;margin:0 0 8px;color:#cbd5e1;}
  .grid{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:8px;}
  .metric{border:1px solid #263244;border-radius:8px;padding:8px;background:#111827;}
  .metric small{display:block;color:#94a3b8;font-size:11px;margin-bottom:2px;}
  .metric strong{font-size:18px;font-variant-numeric:tabular-nums;}
  .leaflet-container{background:#1e293b;}
  @media (max-width:900px){.maps{grid-template-columns:1fr;}.wrap{grid-template-rows:auto 1fr 240px;}.bottom{grid-template-columns:1fr;}.overview{display:none}.grid{grid-template-columns:repeat(2,minmax(120px,1fr));}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>B04/B4 Emergency Destination Animation</h1>
    <div class="controls">
      <button id="play">Play</button>
      <button class="secondary" id="reset">Reset</button>
      <input type="range" id="seek" min="0" max="1000" value="0">
      <span class="clock" id="clock">t = 0.0s</span>
      <select id="rate"><option>1</option><option selected>4</option><option>8</option><option>16</option></select>
    </div>
  </header>
  <main class="maps">
    <section class="panel"><div class="map" id="mapB04"></div><div class="tag" id="tagB04"></div></section>
    <section class="panel"><div class="map" id="mapB4"></div><div class="tag" id="tagB4"></div></section>
  </main>
  <section class="bottom">
    <div class="overview"><div class="map" id="mapOverview"></div><div class="label">Route overview</div></div>
    <div class="stats">
      <h2>Run __RUN_ID__ / target edge __TARGET_EDGE__</h2>
      <div class="grid" id="statsGrid"></div>
    </div>
  </section>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA = __DATA__;
const COLORS = {B04:"__B04COLOR__", B4:"__B4COLOR__"};
const TILES = "__TILES__";
const ATTR = "__ATTR__";
const MODES = ["B04", "B4"];
const FOLLOW_ZOOM = 17;
let now = 0, playing = false, rate = 4, lastFrame = null;
const tMax = Math.max(...MODES.map(mode => DATA.modes[mode].travel_time_sec));

function speedColor(kmh){
  if(kmh < 5) return "#991b1b";
  if(kmh < 15) return "#dc2626";
  if(kmh < 30) return "#f59e0b";
  if(kmh < 45) return "#10b981";
  return "#2563eb";
}
function indexAt(points,t){
  let lo=0, hi=points.length-1, result=0;
  while(lo<=hi){const mid=(lo+hi)>>1;if(points[mid].t_rel<=t){result=mid;lo=mid+1;}else{hi=mid-1;}}
  return result;
}
function lerp(a,b,f){return a+(b-a)*f;}
function stateAt(points,t){
  if(!points.length) return null;
  if(t<=points[0].t_rel) return points[0];
  if(t>=points[points.length-1].t_rel) return {...points[points.length-1], arrived:true};
  const index=indexAt(points,t), a=points[index], b=points[Math.min(index+1,points.length-1)];
  const span=b.t_rel-a.t_rel, f=span ? (t-a.t_rel)/span : 0;
  return {t_rel:t, lat:lerp(a.lat,b.lat,f), lon:lerp(a.lon,b.lon,f), speed_kmh:lerp(a.speed_kmh,b.speed_kmh,f), dist_m:lerp(a.dist_m,b.dist_m,f), edge:a.edge};
}
function makeMap(id){
  const map=L.map(id,{zoomControl:false,attributionControl:false,preferCanvas:true});
  L.tileLayer(TILES,{maxZoom:19,attribution:ATTR}).addTo(map);
  return map;
}
function bgByTime(modeData){
  const byTime = {};
  modeData.background.forEach(snap => byTime[Math.round(snap.t_rel)] = snap.vehicles);
  return byTime;
}
function makePanel(mode){
  const data=DATA.modes[mode];
  const map=makeMap("map"+mode);
  const bounds=L.latLngBounds(data.route_polyline);
  map.fitBounds(bounds,{padding:[24,24]});
  L.polyline(data.route_polyline,{color:COLORS[mode],weight:4,opacity:.48}).addTo(map);
  L.circleMarker([data.destination.lat,data.destination.lon],{radius:8,color:"#fff",weight:2,fillColor:"#16a34a",fillOpacity:1}).bindTooltip("Destination").addTo(map);
  const marker=L.circleMarker(data.route_polyline[0],{radius:9,color:"#fff",weight:2,fillColor:COLORS[mode],fillOpacity:1}).addTo(map);
  const bg=L.layerGroup().addTo(map);
  const events=L.layerGroup().addTo(map);
  if(mode === "B4"){
    data.signal_events.slice(0,200).forEach(event => L.circleMarker([event.lat,event.lon],{radius:3,color:"#f8fafc",weight:1,fillColor:"#f97316",fillOpacity:.85}).bindTooltip(event.action_type || "signal").addTo(events));
  }
  return {mode,data,map,marker,bg,bgByT:bgByTime(data)};
}
const panels = Object.fromEntries(MODES.map(mode => [mode, makePanel(mode)]));
const overview=makeMap("mapOverview");
overview.fitBounds(L.latLngBounds(DATA.modes.B04.route_polyline),{padding:[14,14]});
MODES.forEach(mode => L.polyline(DATA.modes[mode].route_polyline,{color:COLORS[mode],weight:3,opacity:.55}).addTo(overview));
const ovDots=Object.fromEntries(MODES.map(mode => [mode, L.circleMarker(DATA.modes[mode].route_polyline[0],{radius:6,color:"#fff",weight:1,fillColor:COLORS[mode],fillOpacity:1}).addTo(overview)]));

function renderPanel(panel){
  const mode=panel.mode, data=panel.data, capped=Math.min(now,data.travel_time_sec);
  const st=stateAt(data.emergency,capped);
  if(!st) return;
  const point=[st.lat,st.lon], arrived=now>=data.travel_time_sec;
  panel.marker.setLatLng(point).setStyle({fillColor:arrived ? "#16a34a" : speedColor(st.speed_kmh)});
  panel.map.setView(point,FOLLOW_ZOOM,{animate:false});
  ovDots[mode].setLatLng(point);
  panel.bg.clearLayers();
  const nearby=panel.bgByT[Math.round(capped)] || [];
  nearby.forEach(v => L.circleMarker([v.lat,v.lon],{radius:3.5,color:"#cbd5e1",weight:1,fillColor:"#94a3b8",fillOpacity:.75}).addTo(panel.bg));
  const progress=Math.min(100,Math.round(st.dist_m / data.route_length_m * 100));
  document.getElementById("tag"+mode).innerHTML =
    `<b style="color:${COLORS[mode]}">${mode} ${arrived ? "arrived" : "en route"}</b>` +
    `time <span>${capped.toFixed(1)}</span> / <span>${data.travel_time_sec.toFixed(0)}</span>s<br>` +
    `speed <span>${arrived ? 0 : st.speed_kmh.toFixed(1)}</span> km/h / progress <span>${progress}</span>%<br>` +
    `edge <span>${arrived ? data.arrival_edge : st.edge}</span><br>` +
    `nearby vehicles <span>${nearby.length}</span>`;
}
function renderStats(){
  const grid=document.getElementById("statsGrid");
  const b04=DATA.modes.B04, b4=DATA.modes.B4;
  const delta=b4.travel_time_sec-b04.travel_time_sec;
  const improvement=b04.travel_time_sec-b4.travel_time_sec;
  const rows=[
    ["B04 travel", `${b04.travel_time_sec.toFixed(0)}s`],
    ["B4 travel", `${b4.travel_time_sec.toFixed(0)}s`],
    ["B4 minus B04", `${delta.toFixed(0)}s`],
    ["B4 improvement", `${improvement.toFixed(0)}s`],
    ["B04 waiting", `${b04.waiting_time_sec.toFixed(0)}s`],
    ["B4 waiting", `${b4.waiting_time_sec.toFixed(0)}s`],
    ["B4 signal events", `${(b4.signal_events||[]).length}`],
    ["Arrival edge", DATA.meta.target_edge],
  ];
  grid.innerHTML=rows.map(([label,value])=>`<div class="metric"><small>${label}</small><strong>${value}</strong></div>`).join("");
}
function render(){
  MODES.forEach(mode => renderPanel(panels[mode]));
  document.getElementById("seek").value = Math.round(now / tMax * 1000);
  document.getElementById("clock").textContent = `t = ${now.toFixed(1)}s / ${tMax.toFixed(0)}s`;
}
function frame(ts){
  if(!playing) return;
  if(lastFrame !== null){
    now += (ts-lastFrame)/1000*rate;
    if(now >= tMax){now=tMax;playing=false;document.getElementById("play").textContent="Play";}
  }
  lastFrame=ts;render();
  if(playing) requestAnimationFrame(frame);
}
document.getElementById("play").onclick=function(){
  if(now >= tMax) now=0;
  playing=!playing;
  this.textContent=playing ? "Pause" : "Play";
  lastFrame=null;
  if(playing) requestAnimationFrame(frame);
};
document.getElementById("reset").onclick=function(){now=0;playing=false;document.getElementById("play").textContent="Play";render();};
document.getElementById("rate").onchange=function(){rate=parseFloat(this.value);};
document.getElementById("seek").oninput=function(){now=parseFloat(this.value)/1000*tMax;render();};
renderStats();
setTimeout(()=>{MODES.forEach(mode=>panels[mode].map.invalidateSize());overview.invalidateSize();render();},200);
render();
</script>
</body>
</html>
"""


def write_html(doc: dict[str, Any], output_path: Path) -> None:
    html = (
        HTML_TEMPLATE
        .replace("__TITLE__", "Compact V9 B04/B4 Destination Animation")
        .replace("__RUN_ID__", str(doc["run_id"]))
        .replace("__TARGET_EDGE__", str(doc["meta"]["target_edge"]))
        .replace("__DATA__", json.dumps(doc, ensure_ascii=False))
        .replace("__TILES__", MAP_TILES)
        .replace("__ATTR__", MAP_ATTRIBUTION)
        .replace("__B04COLOR__", MODE_COLORS["B04"])
        .replace("__B4COLOR__", MODE_COLORS["B4"])
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build B04/B4 destination animation from FCD.")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--run-root", type=Path, default=RUN_ROOT)
    parser.add_argument("--bg-radius-m", type=float, default=250.0)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-html", type=Path, default=DEFAULT_OUTPUT_HTML)
    args = parser.parse_args(argv)
    try:
        doc = build_doc(args)
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_html(doc, args.output_html)
    except (B04B4AnimationError, FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"json": rel(args.output_json), "html": rel(args.output_html)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
