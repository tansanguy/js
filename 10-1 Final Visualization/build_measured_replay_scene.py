#!/usr/bin/env python3
"""Build a raw measured replay scene from one fixed-depart SUMO run.

This builder intentionally avoids presentation traffic synthesis.  Every
vehicle comes from FCD, every signal timeline comes from tls_states.csv, and
algorithm text comes from signal_events.csv.  The HTML only interpolates and
draws the measured data.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import sumolib  # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THIS_DIR = Path(__file__).resolve().parent
DEFAULT_METRICS_ROOT = PROJECT_ROOT / "results/metrics/compact_v9_final_destination_validation"
DEFAULT_NET_FILE = THIS_DIR / "10-1_jungbu_compact_v9_B04_global_reality_s1forced_presentation.net.xml"


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def safe_float(value: Any, default: float = 0.0) -> float:
    if value in {None, ""}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"missing {label}: {rel(path)}")


def fcd_xy_to_lat_lon(net: Any, x: float, y: float) -> tuple[float, float]:
    # SUMO FCD can be emitted either in network XY or in geographic lon/lat.
    # Treat plausible Seoul lon/lat values as already converted.
    if 120.0 <= x <= 132.0 and 30.0 <= y <= 40.0:
        return y, x
    lon, lat = net.convertXY2LonLat(x, y)
    return float(lat), float(lon)


def resolve_repeat_run_dir(run_dir: Path, repeat_id: str) -> Path:
    if (run_dir / "fcd.xml").is_file():
        return run_dir
    candidates = []
    if run_dir.name == repeat_id:
        candidates.extend(sorted(run_dir.parent.glob(f"*/{repeat_id}/fcd.xml")))
    candidates.extend(sorted(run_dir.glob(f"*/{repeat_id}/fcd.xml")))
    candidates.extend(sorted(run_dir.glob("**/fcd.xml")))
    return candidates[0].parent if candidates else run_dir


def mode_artifacts(metrics_root: Path, run_id: str, route_id: str, repeat_id: str) -> dict[str, dict[str, Path]]:
    manifest = metrics_root / run_id / "final" / "task_manifest.csv"
    if not manifest.is_file():
        candidates = sorted((metrics_root / run_id).glob("robust_final/top_*/final/task_manifest.csv"))
        if candidates:
            manifest = candidates[0]
    require_file(manifest, "task manifest")
    rows = [
        row
        for row in read_csv(manifest)
        if row.get("route_id") == route_id and row.get("repeat_id") == repeat_id
    ]
    if not rows:
        raise SystemExit(f"no task manifest rows for route={route_id} repeat={repeat_id}")
    out: dict[str, dict[str, Path]] = {}
    for row in rows:
        mode = row.get("mode", "")
        if mode not in {"B04", "B4"}:
            continue
        run_dir = resolve_repeat_run_dir(PROJECT_ROOT / row.get("run_dir", ""), repeat_id)
        out[mode] = {
            "run_dir": run_dir,
            "fcd": run_dir / "fcd.xml",
            "tls_states": run_dir / "tls_states.csv",
            "signal_events": run_dir / "signal_events.csv",
            "route_xml": PROJECT_ROOT / row.get("route_xml", ""),
        }
    for mode in ("B04", "B4"):
        if mode not in out:
            raise SystemExit(f"missing {mode} manifest row")
        require_file(out[mode]["fcd"], f"{mode} fcd.xml")
        require_file(out[mode]["tls_states"], f"{mode} tls_states.csv")
        require_file(out[mode]["route_xml"], f"{mode} EV route xml")
    return out


def ev_ids_from_route(route_xml: Path) -> set[str]:
    root = ET.parse(route_xml).getroot()
    ids = set()
    for tag in ("vehicle", "trip", "flow"):
        for node in root.findall(f".//{tag}"):
            vid = node.get("id")
            if vid:
                ids.add(vid)
    return ids


def choose_ev_id(tracks: dict[str, list[list[Any]]], route_ev_ids: set[str]) -> str:
    for vid in route_ev_ids:
        if vid in tracks:
            return vid
    hints = ("emergency", "fire", "truck", "ev", "ambulance")
    hinted = [vid for vid in tracks if any(hint in vid.lower() for hint in hints)]
    if hinted:
        return sorted(hinted, key=lambda vid: len(tracks[vid]), reverse=True)[0]
    return sorted(tracks, key=lambda vid: len(tracks[vid]), reverse=True)[0] if tracks else ""


def parse_fcd(path: Path, net: Any, route_xml: Path, sample_period: float) -> dict[str, Any]:
    tracks: dict[str, list[list[Any]]] = {}
    touched_lanes: set[str] = set()
    start_t: float | None = None
    end_t = 0.0
    last_kept_by_vehicle: dict[str, float] = {}
    vehicle_count_by_t: dict[float, int] = {}
    for event, elem in ET.iterparse(path, events=("end",)):
        if elem.tag != "timestep":
            continue
        t = round(safe_float(elem.get("time")), 3)
        if start_t is None:
            start_t = t
        end_t = max(end_t, t)
        count = 0
        for vehicle in elem.findall("vehicle"):
            vid = str(vehicle.get("id", ""))
            if not vid:
                continue
            prev_t = last_kept_by_vehicle.get(vid)
            if prev_t is not None and t - prev_t < sample_period - 1e-9:
                continue
            x = safe_float(vehicle.get("x"))
            y = safe_float(vehicle.get("y"))
            lat, lon = fcd_xy_to_lat_lon(net, x, y)
            lane = str(vehicle.get("lane", ""))
            if lane:
                touched_lanes.add(lane)
            tracks.setdefault(vid, []).append([
                round(t, 3),
                round(float(lat), 7),
                round(float(lon), 7),
                round(safe_float(vehicle.get("speed")) * 3.6, 2),
                round(safe_float(vehicle.get("angle")), 1),
                lane,
                round(safe_float(vehicle.get("pos")), 2),
            ])
            last_kept_by_vehicle[vid] = t
            count += 1
        vehicle_count_by_t[t] = count
        elem.clear()
    ev_id = choose_ev_id(tracks, ev_ids_from_route(route_xml))
    return {
        "start_t": start_t or 0.0,
        "end_t": end_t,
        "vehicle_count": len(tracks),
        "sample_count": sum(len(points) for points in tracks.values()),
        "ev_id": ev_id,
        "ev_track": tracks.get(ev_id, []),
        "vehicle_tracks": tracks,
        "touched_lanes": sorted(touched_lanes),
        "vehicle_count_by_t": [[t, vehicle_count_by_t[t]] for t in sorted(vehicle_count_by_t)],
    }


def find_key(fieldnames: list[str], candidates: tuple[str, ...], contains: tuple[str, ...] = ()) -> str:
    lowered = {name.lower(): name for name in fieldnames}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    for name in fieldnames:
        low = name.lower()
        if any(token in low for token in contains):
            return name
    return ""


def parse_tls_states(path: Path) -> dict[str, Any]:
    rows = read_csv(path)
    if not rows:
        return {"timelines": {}, "row_count": 0, "raw_columns": []}
    fieldnames = list(rows[0].keys())
    time_key = find_key(fieldnames, ("time", "t", "sim_time", "t_abs"), ("time",))
    tls_key = find_key(fieldnames, ("tls_id", "id", "tl_id"), ("tls",))
    state_key = find_key(fieldnames, ("state", "tls_state", "value"), ("state",))
    phase_key = find_key(fieldnames, ("phase", "phase_index", "phase_id"), ("phase",))
    timelines: dict[str, list[list[Any]]] = {}
    for row in rows:
        tls_id = row.get(tls_key, "") if tls_key else ""
        if not tls_id:
            continue
        state = row.get(state_key, "") if state_key else ""
        if not state and phase_key:
            state = row.get(phase_key, "")
        timelines.setdefault(tls_id, []).append([round(safe_float(row.get(time_key)), 3), state])
    return {
        "timelines": timelines,
        "row_count": len(rows),
        "raw_columns": fieldnames,
        "keys": {"time": time_key, "tls_id": tls_key, "state": state_key, "phase": phase_key},
    }


def parse_signal_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for row in read_csv(path):
        item: dict[str, Any] = {}
        for key, value in row.items():
            if value == "":
                continue
            if key in {"t_abs", "t_rel", "Lq_merge_m", "Q_th_merge_m", "n_occ_runtime_veh", "n_need_proxy_veh"}:
                item[key] = safe_float(value)
            else:
                item[key] = value
        events.append(item)
    return events


def lane_shape(net: Any, lane_id: str) -> dict[str, Any] | None:
    try:
        lane = net.getLane(lane_id)
    except Exception:
        return None
    points = []
    for x, y in lane.getShape():
        lon, lat = net.convertXY2LonLat(float(x), float(y))
        points.append([round(float(lat), 7), round(float(lon), 7)])
    return {"id": lane_id, "edge_id": lane.getEdge().getID(), "shape": points}


def compact_tracks(tracks: dict[str, list[list[Any]]], ev_id: str) -> dict[str, list[list[Any]]]:
    return {vid: pts for vid, pts in tracks.items() if vid != ev_id}


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    net_file = args.net_file.resolve()
    require_file(net_file, "SUMO net")
    net = sumolib.net.readNet(str(net_file), withInternal=True)
    artifacts = mode_artifacts(args.metrics_root.resolve(), args.run_id, args.route_id, args.repeat_id)
    modes: dict[str, Any] = {}
    touched_lanes: set[str] = set()
    for mode in ("B04", "B4"):
        mode_art = artifacts[mode]
        fcd = parse_fcd(mode_art["fcd"], net, mode_art["route_xml"], args.sample_period)
        touched_lanes.update(fcd["touched_lanes"])
        tls = parse_tls_states(mode_art["tls_states"])
        signal_events = parse_signal_events(mode_art["signal_events"])
        modes[mode] = {
            "run_dir": rel(mode_art["run_dir"]),
            "fcd": rel(mode_art["fcd"]),
            "tls_states": rel(mode_art["tls_states"]),
            "signal_events": rel(mode_art["signal_events"]) if mode_art["signal_events"].is_file() else "",
            "route_xml": rel(mode_art["route_xml"]),
            "start_t": fcd["start_t"],
            "end_t": fcd["end_t"],
            "vehicle_count": fcd["vehicle_count"],
            "sample_count": fcd["sample_count"],
            "ev_id": fcd["ev_id"],
            "ev_track": fcd["ev_track"],
            "vehicle_tracks": compact_tracks(fcd["vehicle_tracks"], fcd["ev_id"]),
            "vehicle_count_by_t": fcd["vehicle_count_by_t"],
            "tls": tls,
            "algorithm_events": signal_events,
        }
    lanes = [shape for lane_id in sorted(touched_lanes) if (shape := lane_shape(net, lane_id))]
    return {
        "schema": "seoul_fire_station_measured_replay.v1",
        "route_id": args.route_id,
        "repeat_id": args.repeat_id,
        "run_id": args.run_id,
        "net_file": rel(net_file),
        "sample_period_sec": args.sample_period,
        "source_of_truth": "measured FCD + tls_states.csv + signal_events.csv",
        "lanes": lanes,
        "modes": modes,
    }


def render_html(title: str, data_file: Path) -> str:
    data_name = data_file.name
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html,body{{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f172a;color:#f8fafc}}
.wrap{{height:100vh;display:flex;flex-direction:column}}
header{{height:48px;display:flex;align-items:center;gap:8px;padding:0 10px;background:#111827;box-sizing:border-box}}
h1{{font-size:14px;margin:0;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
button,select{{border:0;border-radius:6px;background:#2563eb;color:white;font-weight:800;padding:7px 10px;white-space:nowrap;line-height:1}}
#seek{{width:300px;max-width:28vw;flex:0 1 300px}}
#clock{{white-space:nowrap;font-variant-numeric:tabular-nums}}
.maps{{display:grid;grid-template-columns:1fr 1fr;flex:1;min-height:0}}
.panel{{position:relative;border-right:1px solid #111827}}
.map,.scene{{position:absolute;inset:0}}
.scene{{z-index:650;pointer-events:none}}
.tag{{position:absolute;top:10px;left:10px;z-index:700;background:rgba(15,23,42,.88);border-radius:8px;padding:9px 12px;font-size:12px;line-height:1.45;font-weight:750;max-width:50%}}
@media(max-width:760px){{h1{{display:none}}#seek{{width:120px;max-width:24vw;flex-basis:120px}}button,select{{padding:7px 8px}}.tag{{font-size:11px;max-width:68%}}}}
</style>
</head>
<body>
<div class="wrap">
<header><h1>{title}</h1><button id="play">▶ 재생</button><select id="rate"><option value="1">1x</option><option value="2">2x</option><option value="4">4x</option><option value="8">8x</option></select><input id="seek" type="range" min="0" max="1000" value="0"><span id="clock"></span></header>
<div class="maps"><div class="panel"><div id="mapB04" class="map"></div><canvas id="canvasB04" class="scene"></canvas><div id="tagB04" class="tag"></div></div><div class="panel"><div id="mapB4" class="map"></div><canvas id="canvasB4" class="scene"></canvas><div id="tagB4" class="tag"></div></div></div>
</div>
<script>
const DATA_URL="{data_name}";
const COLORS={{B04:"#dc2626",B4:"#2563eb"}};
let DATA,t=0,T0=0,T1=1,playing=false,last=null,rate=1,panels={{}};
const byId=id=>document.getElementById(id);
function mapPanel(id){{const m=L.map(id,{{zoomControl:false,attributionControl:false}});L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png",{{maxZoom:19}}).addTo(m);return m;}}
function latLng(map,p){{return map.latLngToContainerPoint([p[1],p[2]]);}}
function roundRect(ctx,x,y,w,h,r){{ctx.beginPath();ctx.moveTo(x+r,y);ctx.arcTo(x+w,y,x+w,y+h,r);ctx.arcTo(x+w,y+h,x,y+h,r);ctx.arcTo(x,y+h,x,y,r);ctx.arcTo(x,y,x+w,y,r);ctx.closePath();}}
function trackAt(track,ts){{if(!track||!track.length)return null;if(ts<=track[0][0])return track[0];if(ts>=track[track.length-1][0])return track[track.length-1];let lo=0,hi=track.length-1;while(lo<=hi){{const mid=(lo+hi)>>1;if(track[mid][0]<=ts)lo=mid+1;else hi=mid-1;}}const a=track[Math.max(0,hi)],b=track[Math.min(track.length-1,hi+1)],f=(ts-a[0])/(b[0]-a[0]||1);return [ts,a[1]+(b[1]-a[1])*f,a[2]+(b[2]-a[2])*f,a[3]+(b[3]-a[3])*f,a[4]+(b[4]-a[4])*f,b[5],a[6]+(b[6]-a[6])*f];}}
function drawVehicle(ctx,map,p,color,ev=false){{const q=latLng(map,p);ctx.save();ctx.translate(q.x,q.y);ctx.rotate((p[4]||0)*Math.PI/180);ctx.fillStyle=color;ctx.strokeStyle="#fff";ctx.lineWidth=ev?3:1.2;const w=ev?36:14,h=ev?22:8;roundRect(ctx,-w/2,-h/2,w,h,ev?7:4);ctx.fill();ctx.stroke();if(ev){{ctx.rotate(-(p[4]||0)*Math.PI/180);ctx.fillStyle="#fff";ctx.font="800 12px system-ui";ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText("EV",0,1);}}ctx.restore();}}
function drawLanes(ctx,map){{ctx.save();ctx.strokeStyle="rgba(59,130,246,.18)";ctx.lineWidth=2;for(const lane of DATA.lanes){{ctx.beginPath();for(let i=0;i<lane.shape.length;i++){{const p=map.latLngToContainerPoint(lane.shape[i]);if(i)ctx.lineTo(p.x,p.y);else ctx.moveTo(p.x,p.y);}}ctx.stroke();}}ctx.restore();}}
function visibleEvents(mode,ts){{return (DATA.modes[mode].algorithm_events||[]).filter(e=>(e.t_rel??e.t_abs??0)<=ts&&ts-(e.t_rel??e.t_abs??0)<6).slice(-3).map(e=>`${{e.stage||""}} ${{e.action||""}} ${{e.case||""}}`).join(" · ");}}
function draw(mode){{const panel=panels[mode],map=panel.map,canvas=panel.canvas,ctx=panel.ctx,doc=DATA.modes[mode],ev=trackAt(doc.ev_track,t);if(ev&&(t-panel.lastPan>.5||panel.lastPan<0)){{map.setView([ev[1],ev[2]],16.7,{{animate:false}});panel.lastPan=t;}}const rect=canvas.parentElement.getBoundingClientRect(),dpr=window.devicePixelRatio||1;if(canvas.width!==Math.round(rect.width*dpr)||canvas.height!==Math.round(rect.height*dpr)){{canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);canvas.style.width=rect.width+"px";canvas.style.height=rect.height+"px";}}ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,rect.width,rect.height);drawLanes(ctx,map);let drawn=0;for(const [id,track] of Object.entries(doc.vehicle_tracks)){{const p=trackAt(track,t);if(!p)continue;drawVehicle(ctx,map,p,"#f97316",false);drawn++;}}if(ev)drawVehicle(ctx,map,ev,COLORS[mode],true);byId("tag"+mode).innerHTML=`<b style="color:${{COLORS[mode]}}">${{mode}}</b><br>t ${{t.toFixed(1)}}s · 차량 ${{drawn}} · EV ${{doc.ev_id||"-"}}<br>${{visibleEvents(mode,t)||"measured replay"}}`;}}
function render(){{draw("B04");draw("B4");byId("seek").value=Math.round((t-T0)/(T1-T0)*1000);byId("clock").textContent=`t = ${{t.toFixed(1)}}s / ${{T1.toFixed(0)}}s`;}}
function loop(ts){{if(!playing)return;if(last!=null)t=Math.min(T1,t+(ts-last)/1000*rate);last=ts;if(t>=T1)playing=false;render();if(playing)requestAnimationFrame(loop);}}
fetch(DATA_URL).then(r=>r.json()).then(data=>{{DATA=data;T0=Math.min(DATA.modes.B04.start_t,DATA.modes.B4.start_t);T1=Math.max(DATA.modes.B04.end_t,DATA.modes.B4.end_t);t=T0;for(const mode of ["B04","B4"]){{const map=mapPanel("map"+mode),canvas=byId("canvas"+mode),ev=trackAt(DATA.modes[mode].ev_track,t)||DATA.modes[mode].ev_track[0];panels[mode]={{map,canvas,ctx:canvas.getContext("2d"),lastPan:-1}};if(ev)map.setView([ev[1],ev[2]],16.7,{{animate:false}});map.invalidateSize();map.on("move zoom resize",()=>draw(mode));}}byId("play").onclick=()=>{{playing=!playing;last=null;byId("play").textContent=playing?"⏸ 일시정지":"▶ 재생";if(playing)requestAnimationFrame(loop);}};byId("rate").onchange=e=>rate=Number(e.target.value);byId("seek").oninput=e=>{{t=T0+(T1-T0)*Number(e.target.value)/1000;render();}};render();}}).catch(err=>{{document.body.innerHTML=`<pre style="white-space:pre-wrap;padding:24px;color:#fecaca;background:#111827;height:100vh;box-sizing:border-box">Measured replay load failed\\n${{err.stack||err}}</pre>`;}});
</script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build measured raw replay scene from FCD/TLS/event outputs.")
    parser.add_argument("--plan", type=Path, default=None, help="Optional 10-1_measured_replay_plan.json.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--route-id", default="")
    parser.add_argument("--repeat-id", default="repeat_001")
    parser.add_argument("--metrics-root", type=Path, default=DEFAULT_METRICS_ROOT)
    parser.add_argument("--net-file", type=Path, default=DEFAULT_NET_FILE)
    parser.add_argument("--sample-period", type=float, default=0.1)
    parser.add_argument("--output", type=Path, default=THIS_DIR / "seoul_station_fire_station_measured_replay.html")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.plan and args.plan.is_file():
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        args.run_id = args.run_id or plan.get("measured_run_id", "")
        args.route_id = args.route_id or plan.get("route_id", "")
    if not args.run_id or not args.route_id:
        raise SystemExit("--run-id and --route-id are required")
    output = args.output.resolve()
    data_output = output.with_name(f"{output.stem}_data.json")
    manifest_output = output.with_name(f"{output.stem}_manifest.json")
    payload = build_payload(args)
    data_output.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    html = render_html("10-1 measured raw replay", data_output)
    output.write_text(html, encoding="utf-8")
    manifest = {
        "schema": "10-1_measured_replay_manifest.v1",
        "html": rel(output),
        "data": rel(data_output),
        "run_id": args.run_id,
        "route_id": args.route_id,
        "repeat_id": args.repeat_id,
        "modes": {
            mode: {
                "vehicle_count": doc["vehicle_count"],
                "sample_count": doc["sample_count"],
                "ev_id": doc["ev_id"],
                "fcd": doc["fcd"],
                "tls_states": doc["tls_states"],
                "signal_events": doc["signal_events"],
            }
            for mode, doc in payload["modes"].items()
        },
    }
    manifest_output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output}")
    print(f"Wrote {data_output}")
    print(f"Wrote {manifest_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
