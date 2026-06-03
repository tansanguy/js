"""Build the animated dual-map HTML comparing B0 vs B2 emergency progress.

Layout (option A):
- Two side-by-side Leaflet maps (B0 left, B2 right), each a *follow camera* that
  tracks its own emergency vehicle at street zoom.
- A shared overview mini-map (full corridor) with both emergency positions.
- Real-elapsed-time sync: one clock drives both panels from ``t_rel`` 0..max.
- Speed shown as marker colour + live badge + a time-speed chart with a cursor.
- Background (side-street) vehicles drawn as dots, already radius-filtered by the
  extractor. B2 panel overlays signal-control events.
"""

import json
from pathlib import Path
from typing import Any

from config import MAP_TILES, MAP_ATTRIBUTION, MODE_COLORS

_TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  :root { --b0:__B0COLOR__; --b2:__B2COLOR__; }
  html,body{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#111827;background:#0f172a;}
  .wrap{display:flex;flex-direction:column;height:100vh;}
  header{padding:10px 16px;background:#111827;color:#f9fafb;display:flex;align-items:center;gap:16px;flex-wrap:wrap;}
  header h1{font-size:16px;margin:0;font-weight:700;}
  .controls{display:flex;align-items:center;gap:10px;flex:1;min-width:280px;}
  button{background:#2563eb;color:#fff;border:0;border-radius:6px;padding:6px 14px;font-size:13px;cursor:pointer;}
  button.sec{background:#374151;}
  input[type=range]{flex:1;}
  .clock{font-variant-numeric:tabular-nums;font-size:13px;color:#cbd5e1;min-width:120px;}
  .speedsel{font-size:12px;color:#cbd5e1;}
  .maps{display:grid;grid-template-columns:1fr 1fr;flex:1;min-height:0;}
  .panel{position:relative;border-right:1px solid #0b1220;min-height:0;}
  .panel .map{position:absolute;inset:0;background:#1e293b;}
  .tag{position:absolute;top:10px;left:10px;z-index:500;background:rgba(17,24,39,.85);color:#fff;border-radius:8px;padding:8px 12px;font-size:13px;line-height:1.5;}
  .tag .mode{font-weight:700;font-size:14px;}
  .tag .spd{font-variant-numeric:tabular-nums;font-weight:700;}
  .tag .done{color:#10b981;font-weight:700;}
  .sig{position:absolute;bottom:10px;left:10px;right:10px;z-index:500;background:rgba(17,24,39,.85);color:#e5e7eb;border-radius:8px;padding:6px 10px;font-size:12px;display:none;}
  .bottom{display:grid;grid-template-columns:340px 1fr;gap:0;height:200px;background:#0b1220;}
  .overview{position:relative;border-right:1px solid #0b1220;}
  .overview .map{position:absolute;inset:0;background:#1e293b;}
  .overview .label{position:absolute;top:6px;left:8px;z-index:500;color:#cbd5e1;font-size:11px;background:rgba(17,24,39,.7);padding:2px 6px;border-radius:4px;}
  .chart{position:relative;padding:8px 12px;}
  .chart h3{margin:0 0 4px;font-size:12px;color:#cbd5e1;font-weight:600;}
  .legend{font-size:11px;color:#94a3b8;display:flex;gap:14px;margin-bottom:4px;}
  .legend span{display:inline-flex;align-items:center;gap:4px;}
  .dot{width:10px;height:3px;border-radius:2px;display:inline-block;}
  svg{width:100%;height:130px;display:block;}
  .leaflet-container{background:#1e293b;}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>__TITLE__</h1>
    <div class="controls">
      <button id="play">▶ 재생</button>
      <button id="reset" class="sec">↺ 처음</button>
      <input type="range" id="seek" min="0" max="1000" value="0">
      <span class="clock" id="clock">t = 0.0s</span>
      <span class="speedsel">배속
        <select id="rate"><option>1</option><option selected>4</option><option>8</option><option>16</option></select>×
      </span>
    </div>
  </header>
  <div class="maps">
    <div class="panel">
      <div class="map" id="mapB0"></div>
      <div class="tag"><span class="mode" style="color:var(--b0)">B0 · 신호제어 없음</span><br>
        속도 <span class="spd" id="spdB0">0</span> km/h · 진행 <span id="progB0">0</span>%</div>
    </div>
    <div class="panel">
      <div class="map" id="mapB2"></div>
      <div class="tag"><span class="mode" style="color:var(--b2)">B2 · Corridor Priority</span><br>
        속도 <span class="spd" id="spdB2">0</span> km/h · 진행 <span id="progB2">0</span>%</div>
      <div class="sig" id="sigB2"></div>
    </div>
  </div>
  <div class="bottom">
    <div class="overview">
      <div class="map" id="mapOv"></div>
      <div class="label">전체 경로 (오버뷰)</div>
    </div>
    <div class="chart">
      <h3>시간 — 속도</h3>
      <div class="legend">
        <span><i class="dot" style="background:var(--b0)"></i>B0</span>
        <span><i class="dot" style="background:var(--b2)"></i>B2</span>
        <span id="cmp"></span>
      </div>
      <svg id="spdChart" viewBox="0 0 1000 130" preserveAspectRatio="none"></svg>
    </div>
  </div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
window.__pageErrors=[];
window.addEventListener("error",e=>window.__pageErrors.push({message:e.message,stack:e.error&&e.error.stack}));
const DATA = __DATA__;
const TILES = "__TILES__", ATTR = "__ATTR__";
const COLORS = {B0:"__B0COLOR__", B2:"__B2COLOR__"};
const FOLLOW_ZOOM = 17;

function speedColor(kmh){
  if(kmh<10) return "#7c2d12"; if(kmh<20) return "#dc2626";
  if(kmh<30) return "#f59e0b"; if(kmh<40) return "#10b981"; return "#2563eb";
}
// binary search last index with arr[i].t_rel <= t
function idxAt(arr,t){let lo=0,hi=arr.length-1,r=0;while(lo<=hi){const m=(lo+hi)>>1;if(arr[m].t_rel<=t){r=m;lo=m+1;}else hi=m-1;}return r;}
function lerp(a,b,f){return a+(b-a)*f;}
function emAt(pts,t){
  if(!pts.length) return null;
  if(t<=pts[0].t_rel) return pts[0];
  if(t>=pts[pts.length-1].t_rel) return pts[pts.length-1];
  const i=idxAt(pts,t),a=pts[i],b=pts[Math.min(i+1,pts.length-1)];
  const span=b.t_rel-a.t_rel,f=span?(t-a.t_rel)/span:0;
  return {lat:lerp(a.lat,b.lat,f),lon:lerp(a.lon,b.lon,f),
          speed_kmh:lerp(a.speed_kmh,b.speed_kmh,f),dist_m:lerp(a.dist_m,b.dist_m,f),
          t_rel:t,arrived:b===pts[pts.length-1]&&t>=pts[pts.length-1].t_rel};
}

function makeMap(id){
  const m=L.map(id,{zoomControl:false,attributionControl:false,preferCanvas:true});
  L.tileLayer(TILES,{maxZoom:19,attribution:ATTR}).addTo(m);
  return m;
}

// per-mode follow panel
function Panel(mode){
  const p=DATA.modes[mode];
  const map=makeMap(mode==="B0"?"mapB0":"mapB2");
  L.polyline(p.route_polyline,{color:COLORS[mode],weight:3,opacity:.35}).addTo(map);
  map.setView(p.route_polyline[0],FOLLOW_ZOOM);
  const marker=L.circleMarker(p.route_polyline[0],
    {radius:9,color:"#fff",weight:2,fillColor:COLORS[mode],fillOpacity:1}).addTo(map);
  const bgLayer=L.layerGroup().addTo(map);
  const sigLayer=L.layerGroup().addTo(map);
  // index background snapshots by integer t_rel for quick lookup
  const bgByT={}; p.background.forEach(s=>bgByT[Math.round(s.t_rel)]=s.vehicles);
  return {p,map,marker,bgLayer,sigLayer,bgByT,
    routeLen:DATA.meta.route_length_m,
    shownSig:new Set()};
}

function updatePanel(panel,t,mode){
  const st=emAt(panel.p.emergency,t); if(!st) return;
  const ll=[st.lat,st.lon];
  panel.marker.setLatLng(ll).setStyle({fillColor:speedColor(st.speed_kmh)});
  panel.map.setView(ll,FOLLOW_ZOOM,{animate:false});
  // background dots near current snapshot
  panel.bgLayer.clearLayers();
  const veh=panel.bgByT[Math.round(t)]||[];
  veh.forEach(v=>L.circleMarker([v.lat,v.lon],
    {radius:4,color:"#cbd5e1",weight:1,fillColor:"#94a3b8",fillOpacity:.85}).addTo(panel.bgLayer));
  // readouts
  const arrived=t>=panel.p.travel_time_sec;
  document.getElementById(mode==="B0"?"spdB0":"spdB2").textContent=arrived?"도착":st.speed_kmh.toFixed(0);
  document.getElementById(mode==="B0"?"progB0":"progB2").textContent=
    Math.min(100,Math.round(st.dist_m/panel.routeLen*100));
  // signal events (B2): show events whose t_rel <= t, label the latest within 6s window
  if(mode==="B2"&&panel.p.signal_events){
    panel.sigLayer.clearLayers();
    let recent=null;
    panel.p.signal_events.forEach(e=>{
      if(e.t_rel<=t){
        const fresh=t-e.t_rel<=6;
        L.circleMarker([e.lat,e.lon],{radius:fresh?10:5,
          color:e.action==="request_green"?"#10b981":"#f59e0b",weight:2,
          fillColor:e.action==="request_green"?"#10b981":"#f59e0b",
          fillOpacity:fresh?.6:.25}).addTo(panel.sigLayer);
        if(fresh) recent=e;
      }
    });
    const box=document.getElementById("sigB2");
    if(recent){box.style.display="block";
      const jid=(recent.junction_id||recent.tls_id||"").slice(0,16);
      box.innerHTML=`🚦 <b>${recent.action}</b> @ ${jid} · 잔여 ${recent.remaining_distance_m??"-"} m · t=${recent.t_rel.toFixed(0)}s`;}
    else box.style.display="none";
  }
}

// overview minimap
function buildOverview(){
  const m=makeMap("mapOv");
  const all=[];
  ["B0","B2"].forEach(k=>{const pl=DATA.modes[k].route_polyline;
    L.polyline(pl,{color:COLORS[k],weight:2,opacity:.6}).addTo(m);all.push(...pl);});
  m.fitBounds(L.latLngBounds(all),{padding:[12,12]});
  const dots={B0:L.circleMarker(all[0],{radius:6,color:"#fff",weight:1,fillColor:COLORS.B0,fillOpacity:1}).addTo(m),
              B2:L.circleMarker(all[0],{radius:6,color:"#fff",weight:1,fillColor:COLORS.B2,fillOpacity:1}).addTo(m)};
  return dots;
}

// time-speed chart (static lines + moving cursor)
function buildChart(){
  const svg=document.getElementById("spdChart"),W=1000,H=130,pad=6;
  const tmax=Math.max(DATA.modes.B0.travel_time_sec,DATA.modes.B2.travel_time_sec);
  let smax=1;["B0","B2"].forEach(k=>DATA.modes[k].emergency.forEach(p=>smax=Math.max(smax,p.speed_kmh)));
  const X=t=>pad+(t/tmax)*(W-2*pad), Y=s=>H-pad-(s/smax)*(H-2*pad);
  function path(k){return DATA.modes[k].emergency.map((p,i)=>(i?"L":"M")+X(p.t_rel).toFixed(1)+" "+Y(p.speed_kmh).toFixed(1)).join(" ");}
  svg.innerHTML=
    `<path d="${path('B0')}" fill="none" stroke="${COLORS.B0}" stroke-width="2"/>`+
    `<path d="${path('B2')}" fill="none" stroke="${COLORS.B2}" stroke-width="2"/>`+
    `<line id="cur" x1="${X(0)}" y1="0" x2="${X(0)}" y2="${H}" stroke="#e5e7eb" stroke-width="1" stroke-dasharray="3 3"/>`;
  return {X,tmax};
}

// ---- controller ----
const panels={B0:Panel("B0"),B2:Panel("B2")};
const ovDots=buildOverview();
const chart=buildChart();
const TMAX=Math.max(DATA.modes.B0.travel_time_sec,DATA.modes.B2.travel_time_sec);
document.getElementById("cmp").textContent=
  `B0 ${DATA.modes.B0.travel_time_sec.toFixed(0)}s vs B2 ${DATA.modes.B2.travel_time_sec.toFixed(0)}s `+
  `(−${(DATA.modes.B0.travel_time_sec-DATA.modes.B2.travel_time_sec).toFixed(0)}s)`;

let t=0,playing=false,rate=4,last=null;
const seek=document.getElementById("seek"),clock=document.getElementById("clock");
const cur=()=>document.getElementById("cur");

function render(){
  updatePanel(panels.B0,t,"B0");
  updatePanel(panels.B2,t,"B2");
  const b0=emAt(DATA.modes.B0.emergency,t),b2=emAt(DATA.modes.B2.emergency,t);
  if(b0) ovDots.B0.setLatLng([b0.lat,b0.lon]);
  if(b2) ovDots.B2.setLatLng([b2.lat,b2.lon]);
  cur().setAttribute("x1",chart.X(t));cur().setAttribute("x2",chart.X(t));
  seek.value=Math.round(t/TMAX*1000);
  clock.textContent=`t = ${t.toFixed(1)}s  /  ${TMAX.toFixed(0)}s`;
}
function loop(ts){
  if(!playing) return;
  if(last!=null){t+=(ts-last)/1000*rate; if(t>=TMAX){t=TMAX;playing=false;document.getElementById("play").textContent="▶ 재생";}}
  last=ts;render();
  if(playing) requestAnimationFrame(loop);
}
document.getElementById("play").onclick=function(){
  if(t>=TMAX) t=0;
  playing=!playing;this.textContent=playing?"⏸ 일시정지":"▶ 재생";
  last=null;if(playing) requestAnimationFrame(loop);
};
document.getElementById("reset").onclick=function(){t=0;playing=false;document.getElementById("play").textContent="▶ 재생";render();};
document.getElementById("rate").onchange=function(){rate=parseFloat(this.value);};
seek.oninput=function(){t=this.value/1000*TMAX;render();};
setTimeout(()=>{["B0","B2"].forEach(k=>panels[k].map.invalidateSize());panels.B0&&render();},200);
render();
</script>
</body>
</html>
"""


def build_animated_dual_map_html(doc: dict[str, Any], output_path: Path, title: str) -> None:
    """Render the animation JSON document to a standalone HTML file."""
    html = (
        _TEMPLATE
        .replace("__DATA__", json.dumps(doc, ensure_ascii=False))
        .replace("__TILES__", MAP_TILES)
        .replace("__ATTR__", MAP_ATTRIBUTION)
        .replace("__B0COLOR__", MODE_COLORS["B0"])
        .replace("__B2COLOR__", MODE_COLORS["B2"])
        .replace("__TITLE__", title)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


_SINGLE_TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  :root { --b0:__B0COLOR__; }
  html,body{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#111827;background:#0f172a;}
  .wrap{display:flex;flex-direction:column;height:100vh;}
  header{padding:10px 16px;background:#111827;color:#f9fafb;display:flex;align-items:center;gap:16px;flex-wrap:wrap;}
  header h1{font-size:16px;margin:0;font-weight:700;}
  .controls{display:flex;align-items:center;gap:10px;flex:1;min-width:280px;}
  button{background:#2563eb;color:#fff;border:0;border-radius:6px;padding:6px 14px;font-size:13px;cursor:pointer;}
  button.sec{background:#374151;}
  input[type=range]{flex:1;}
  .clock{font-variant-numeric:tabular-nums;font-size:13px;color:#cbd5e1;min-width:120px;}
  .speedsel{font-size:12px;color:#cbd5e1;}
  .maps{display:grid;grid-template-columns:1fr;flex:1;min-height:0;}
  .panel{position:relative;border-right:1px solid #0b1220;min-height:0;}
  .panel .map{position:absolute;inset:0;background:#1e293b;}
  .tag{position:absolute;top:10px;left:10px;z-index:500;background:rgba(17,24,39,.85);color:#fff;border-radius:8px;padding:8px 12px;font-size:13px;line-height:1.5;}
  .tag .mode{font-weight:700;font-size:14px;}
  .tag .spd{font-variant-numeric:tabular-nums;font-weight:700;}
  .bottom{display:grid;grid-template-columns:340px 1fr;gap:0;height:200px;background:#0b1220;}
  .overview{position:relative;border-right:1px solid #0b1220;}
  .overview .map{position:absolute;inset:0;background:#1e293b;}
  .overview .label{position:absolute;top:6px;left:8px;z-index:500;color:#cbd5e1;font-size:11px;background:rgba(17,24,39,.7);padding:2px 6px;border-radius:4px;}
  .chart{position:relative;padding:8px 12px;}
  .chart h3{margin:0 0 4px;font-size:12px;color:#cbd5e1;font-weight:600;}
  .legend{font-size:11px;color:#94a3b8;display:flex;gap:14px;margin-bottom:4px;}
  .legend span{display:inline-flex;align-items:center;gap:4px;}
  .dot{width:10px;height:3px;border-radius:2px;display:inline-block;}
  svg{width:100%;height:130px;display:block;}
  .leaflet-container{background:#1e293b;}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>__TITLE__</h1>
    <div class="controls">
      <button id="play">▶ 재생</button>
      <button id="reset" class="sec">↺ 처음</button>
      <input type="range" id="seek" min="0" max="1000" value="0">
      <span class="clock" id="clock">t = 0.0s</span>
      <span class="speedsel">배속
        <select id="rate"><option>1</option><option selected>4</option><option>8</option><option>16</option></select>×
      </span>
    </div>
  </header>
  <div class="maps">
    <div class="panel">
      <div class="map" id="mapB0"></div>
      <div class="tag"><span class="mode" style="color:var(--b0)">B0 · 신호제어 없음</span><br>
        <span id="routeLabel"></span><br>
        속도 <span class="spd" id="spdB0">0</span> km/h · 진행 <span id="progB0">0</span>% · 주변 일반차 <span id="bgCount">0</span>대</div>
    </div>
  </div>
  <div class="bottom">
    <div class="overview">
      <div class="map" id="mapOv"></div>
      <div class="label">전체 경로 (오버뷰)</div>
    </div>
    <div class="chart">
      <h3>시간 — 속도</h3>
      <div class="legend">
        <span><i class="dot" style="background:var(--b0)"></i>B0</span>
        <span id="cmp"></span>
      </div>
      <svg id="spdChart" viewBox="0 0 1000 130" preserveAspectRatio="none"></svg>
    </div>
  </div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA = __DATA__;
const TILES = "__TILES__", ATTR = "__ATTR__";
const COLORS = {B0:"__B0COLOR__"};
const FOLLOW_ZOOM = 17;

function speedColor(kmh){
  if(kmh<10) return "#7c2d12"; if(kmh<20) return "#dc2626";
  if(kmh<30) return "#f59e0b"; if(kmh<40) return "#10b981"; return "#2563eb";
}
function idxAt(arr,t){let lo=0,hi=arr.length-1,r=0;while(lo<=hi){const m=(lo+hi)>>1;if(arr[m].t_rel<=t){r=m;lo=m+1;}else hi=m-1;}return r;}
function lerp(a,b,f){return a+(b-a)*f;}
function emAt(pts,t){
  if(!pts.length) return null;
  if(t<=pts[0].t_rel) return pts[0];
  if(t>=pts[pts.length-1].t_rel) return pts[pts.length-1];
  const i=idxAt(pts,t),a=pts[i],b=pts[Math.min(i+1,pts.length-1)];
  const span=b.t_rel-a.t_rel,f=span?(t-a.t_rel)/span:0;
  return {lat:lerp(a.lat,b.lat,f),lon:lerp(a.lon,b.lon,f),
          speed_kmh:lerp(a.speed_kmh,b.speed_kmh,f),dist_m:lerp(a.dist_m,b.dist_m,f),
          t_rel:t,arrived:b===pts[pts.length-1]&&t>=pts[pts.length-1].t_rel};
}
function makeMap(id){
  const m=L.map(id,{zoomControl:false,attributionControl:false,preferCanvas:true});
  L.tileLayer(TILES,{maxZoom:19,attribution:ATTR}).addTo(m);
  return m;
}

function Panel(mode){
  const p=DATA.modes[mode];
  const map=makeMap("mapB0");
  map.setView(p.route_polyline[0],FOLLOW_ZOOM);
  L.polyline(p.route_polyline,{color:COLORS[mode],weight:3,opacity:.35,renderer:L.svg()}).addTo(map);
  const marker=L.circleMarker(p.route_polyline[0],
    {radius:9,color:"#fff",weight:2,fillColor:COLORS[mode],fillOpacity:1}).addTo(map);
  const bgLayer=L.layerGroup().addTo(map);
  const bgByT={}; p.background.forEach(s=>bgByT[Math.round(s.t_rel)]=s.vehicles);
  return {p,map,marker,bgLayer,bgByT,routeLen:DATA.meta.route_length_m};
}

function updatePanel(panel,t,mode){
  const st=emAt(panel.p.emergency,t); if(!st) return;
  const ll=[st.lat,st.lon];
  panel.marker.setLatLng(ll).setStyle({fillColor:speedColor(st.speed_kmh)});
  panel.map.setView(ll,FOLLOW_ZOOM,{animate:false});
  panel.bgLayer.clearLayers();
  const veh=panel.bgByT[Math.round(t)]||[];
  veh.forEach(v=>L.circleMarker([v.lat,v.lon],
    {radius:4,color:"#cbd5e1",weight:1,fillColor:"#94a3b8",fillOpacity:.85}).addTo(panel.bgLayer));
  const arrived=t>=panel.p.travel_time_sec;
  document.getElementById("spdB0").textContent=arrived?"도착":st.speed_kmh.toFixed(0);
  document.getElementById("progB0").textContent=Math.min(100,Math.round(st.dist_m/panel.routeLen*100));
  document.getElementById("bgCount").textContent=veh.length;
}

function buildOverview(){
  const m=makeMap("mapOv");
  const pl=DATA.modes.B0.route_polyline;
  m.fitBounds(L.latLngBounds(pl),{padding:[12,12]});
  L.polyline(pl,{color:COLORS.B0,weight:2,opacity:.6,renderer:L.svg()}).addTo(m);
  return L.circleMarker(pl[0],{radius:6,color:"#fff",weight:1,fillColor:COLORS.B0,fillOpacity:1,renderer:L.svg()}).addTo(m);
}

function buildChart(){
  const svg=document.getElementById("spdChart"),W=1000,H=130,pad=6;
  const tmax=DATA.modes.B0.travel_time_sec;
  let smax=1;DATA.modes.B0.emergency.forEach(p=>smax=Math.max(smax,p.speed_kmh));
  const X=t=>pad+(t/tmax)*(W-2*pad), Y=s=>H-pad-(s/smax)*(H-2*pad);
  const path=DATA.modes.B0.emergency.map((p,i)=>(i?"L":"M")+X(p.t_rel).toFixed(1)+" "+Y(p.speed_kmh).toFixed(1)).join(" ");
  svg.innerHTML=`<path d="${path}" fill="none" stroke="${COLORS.B0}" stroke-width="2"/>`+
    `<line id="cur" x1="${X(0)}" y1="0" x2="${X(0)}" y2="${H}" stroke="#e5e7eb" stroke-width="1" stroke-dasharray="3 3"/>`;
  return {X,tmax};
}

const panels={B0:Panel("B0")};
document.getElementById("routeLabel").textContent=DATA.modes.B0.label_ko||DATA.modes.B0.route_id||"";
const ovDot=buildOverview();
const chart=buildChart();
const TMAX=DATA.modes.B0.travel_time_sec;
document.getElementById("cmp").textContent=`B0 ${DATA.modes.B0.travel_time_sec.toFixed(0)}s · depart ${DATA.modes.B0.depart_time_sec.toFixed(0)}s`;

let t=0,playing=false,rate=4,last=null;
const seek=document.getElementById("seek"),clock=document.getElementById("clock");
const cur=()=>document.getElementById("cur");

function render(){
  updatePanel(panels.B0,t,"B0");
  const b0=emAt(DATA.modes.B0.emergency,t);
  if(b0) ovDot.setLatLng([b0.lat,b0.lon]);
  cur().setAttribute("x1",chart.X(t));cur().setAttribute("x2",chart.X(t));
  seek.value=Math.round(t/TMAX*1000);
  clock.textContent=`t = ${t.toFixed(1)}s  /  ${TMAX.toFixed(0)}s`;
}
function loop(ts){
  if(!playing) return;
  if(last!=null){t+=(ts-last)/1000*rate; if(t>=TMAX){t=TMAX;playing=false;document.getElementById("play").textContent="▶ 재생";}}
  last=ts;render();
  if(playing) requestAnimationFrame(loop);
}
document.getElementById("play").onclick=function(){
  if(t>=TMAX) t=0;
  playing=!playing;this.textContent=playing?"⏸ 일시정지":"▶ 재생";
  last=null;if(playing) requestAnimationFrame(loop);
};
document.getElementById("reset").onclick=function(){t=0;playing=false;document.getElementById("play").textContent="▶ 재생";render();};
document.getElementById("rate").onchange=function(){rate=parseFloat(this.value);};
seek.oninput=function(){t=this.value/1000*TMAX;render();};
setTimeout(()=>{panels.B0.map.invalidateSize();render();},200);
render();
</script>
</body>
</html>
"""


def build_animated_single_map_html(doc: dict[str, Any], output_path: Path, title: str) -> None:
    """Render one B0 follow-camera animation using the 04_visualize vehicle style."""
    html = (
        _SINGLE_TEMPLATE
        .replace("__DATA__", json.dumps(doc, ensure_ascii=False))
        .replace("__TILES__", MAP_TILES)
        .replace("__ATTR__", MAP_ATTRIBUTION)
        .replace("__B0COLOR__", MODE_COLORS["B0"])
        .replace("__TITLE__", title)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
