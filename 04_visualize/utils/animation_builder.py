"""Build the animated dual-map HTML comparing B0 vs B2 emergency progress.

Layout (option A):
- Two side-by-side Leaflet maps (B0 left, B2 right), each a *follow camera* that
  tracks its own emergency vehicle at street zoom.
- A shared overview mini-map (full corridor) with both emergency positions.
- Real-elapsed-time sync: one clock drives both panels from ``t_rel`` 0..max.
- Emergency marker uses a fixed mode colour; speed is shown as a live badge plus
  the time-speed chart. The *signal state* is carried by traffic-light icons
  placed at the real TLS positions (``DATA.traffic_lights`` + per-mode
  ``tls_states``), which recolour red/green as the vehicle passes and highlight
  the next light ahead — replacing the old emergency-colour signal cue.
- Background (side-street) vehicles drawn as dots, already radius-filtered by the
  extractor.
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
  .tag .nextsig{font-variant-numeric:tabular-nums;font-weight:700;}
  /* signal-light icons (placed at real TLS positions, recoloured per state) */
  .tlwrap{background:transparent;border:0;}
  .tl{display:flex;flex-direction:column;gap:2px;padding:3px;background:#0b1220;border:1px solid #475569;border-radius:4px;line-height:0;}
  .tl i{width:11px;height:11px;border-radius:50%;background:#1f2937;display:block;}
  .tlwrap[data-state="red"] .tl i.r{background:#ff0000;box-shadow:0 0 10px #ff0000,0 0 4px #ff0000;}
  .tlwrap[data-state="yellow"] .tl i.y{background:#ffd400;box-shadow:0 0 10px #ffd400,0 0 4px #ffd400;}
  .tlwrap[data-state="green"] .tl i.g{background:#00e000;box-shadow:0 0 10px #00e000,0 0 4px #00e000;}
  .tlwrap[data-state="off"]{opacity:.4;}
  .tlwrap.next .tl{border-color:#fde047;box-shadow:0 0 0 3px rgba(253,224,71,.85);}
  .siglegend{display:flex;gap:12px;font-size:12px;color:#cbd5e1;align-items:center;}
  .siglegend i{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:3px;vertical-align:middle;}
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
    <div class="siglegend">
      <span><i style="background:#ff0000"></i>정지신호</span>
      <span><i style="background:#ffd400"></i>주의(황)</span>
      <span><i style="background:#00e000"></i>통과신호</span>
    </div>
  </header>
  <div class="maps">
    <div class="panel">
      <div class="map" id="mapB0"></div>
      <div class="tag"><span class="mode" style="color:var(--b0)">B0 · 신호제어 없음</span><br>
        속도 <span class="spd" id="spdB0">0</span> km/h · 진행 <span id="progB0">0</span>% · 다음신호 <span class="nextsig" id="nsB0">–</span></div>
    </div>
    <div class="panel">
      <div class="map" id="mapB2"></div>
      <div class="tag"><span class="mode" style="color:var(--b2)">B2 · Corridor Priority</span><br>
        속도 <span class="spd" id="spdB2">0</span> km/h · 진행 <span id="progB2">0</span>% · 다음신호 <span class="nextsig" id="nsB2">–</span></div>
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
const DATA = __DATA__;
const TILES = "__TILES__", ATTR = "__ATTR__";
const COLORS = {B0:"__B0COLOR__", B2:"__B2COLOR__"};
const FOLLOW_ZOOM = 17;
const BASEMAP = "__BASEMAP__";  // none | osm | carto_light | carto_light_nolabels | carto_dark
const _CARTO = "© OpenStreetMap, © CARTO";
const TILE_URLS = {
  osm:[TILES,ATTR],
  // light_all: roads + road/place/district labels, water, parks — no shop POIs/icons,
  // buildings barely rendered. Realistic but clean.
  carto_light:["https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",_CARTO],
  carto_light_nolabels:["https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png",_CARTO],
  carto_dark:["https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",_CARTO],
};
// solid canvas colour when no basemap tiles are drawn (light so black cars/lanes pop)
const NO_TILE_BG = (BASEMAP==="carto_dark") ? "#0b1324" : "#eef2f6";

// last state at/before t in a compressed [[t,state],...] signal timeline
function stateAt(tl,tt){const a=tl.states;let r=a.length?a[0][1]:"off";for(let i=0;i<a.length;i++){if(a[i][0]<=tt)r=a[i][1];else break;}return r;}
const SIG_LABEL={red:"🔴 정지",green:"🟢 통과",yellow:"🟡",off:"–"};
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
  const t=TILE_URLS[BASEMAP];
  if(t){ L.tileLayer(t[0],{maxZoom:19,attribution:t[1]}).addTo(m); }
  else { m.getContainer().style.background=NO_TILE_BG; }  // no basemap: solid canvas
  return m;
}

// per-mode follow panel
function drawLanes(map){
  // SUMO per-lane geometry near the route: one thin line per lane, so a 2-lane
  // road shows as two parallel lines.
  (DATA.lanes||[]).forEach(e=>{
    L.polyline(e.coords,{color:"#334155",weight:2.2,opacity:.55,
      lineCap:"round",lineJoin:"round"}).addTo(map);
  });
}

function Panel(mode){
  const p=DATA.modes[mode];
  const map=makeMap(mode==="B0"?"mapB0":"mapB2");
  drawLanes(map);
  L.polyline(p.route_polyline,{color:COLORS[mode],weight:3,opacity:.35}).addTo(map);
  map.setView(p.route_polyline[0],FOLLOW_ZOOM);
  const marker=L.circleMarker(p.route_polyline[0],
    {radius:9,color:"#fff",weight:2,fillColor:COLORS[mode],fillOpacity:1}).addTo(map);
  const bgLayer=L.layerGroup().addTo(map);
  // signal-light icons at real TLS positions, recoloured each frame
  const tlsStates=p.tls_states||{};
  const tlMarkers=(DATA.traffic_lights||[]).map(t=>({
    el:L.marker([t.lat,t.lon],{interactive:false,keyboard:false,
      icon:L.divIcon({className:"tlwrap",iconSize:[23,44],iconAnchor:[11,22],
        html:'<div class="tl"><i class="r"></i><i class="y"></i><i class="g"></i></div>'})}).addTo(map),
    states:tlsStates[t.tls_id]||[[0,"off"]],
    s:(t.s_m&&t.s_m[mode]!=null)?t.s_m[mode]:null}));
  // index background snapshots by integer t_rel as {id:[lat,lon]} so each
  // vehicle can be interpolated by id between one-second samples (continuous
  // motion instead of a per-frame clear/redraw that makes dots blink).
  const bgByT={}; p.background.forEach(s=>{const mm={};s.vehicles.forEach(v=>{mm[v.id]=[v.lat,v.lon];});bgByT[Math.round(s.t_rel)]=mm;});
  return {p,map,marker,bgLayer,tlMarkers,bgByT,bgMarkers:{},routeLen:DATA.meta.route_length_m};
}

function updatePanel(panel,t,mode){
  const st=emAt(panel.p.emergency,t); if(!st) return;
  const ll=[st.lat,st.lon];
  const arrived=t>=panel.p.travel_time_sec;
  // emergency marker keeps a fixed mode colour now; signal state lives on the lights
  panel.marker.setLatLng(ll).setStyle({fillColor:arrived?"#16a34a":COLORS[mode]});
  panel.map.setView(ll,FOLLOW_ZOOM,{animate:false});
  // background vehicles: interpolate each by id between the floor/ceil one-second
  // snapshots and reuse a persistent marker per id, so vehicles glide instead of
  // blinking. Markers that leave the follow radius are removed; new ones appear.
  const t0=Math.floor(t),s0=panel.bgByT[t0]||{},s1=panel.bgByT[t0+1]||{},f=t-t0;
  const pool=panel.bgMarkers,seen={};
  for(const id in s0){
    const a=s0[id],b=s1[id];
    const lat=b?a[0]+(b[0]-a[0])*f:a[0], lon=b?a[1]+(b[1]-a[1])*f:a[1];
    seen[id]=1;
    if(pool[id]) pool[id].setLatLng([lat,lon]);
    else pool[id]=L.circleMarker([lat,lon],{radius:4.5,color:"#ffffff",weight:1,fillColor:"#0a0a0a",fillOpacity:1,interactive:false}).addTo(panel.bgLayer);
  }
  for(const id in pool){ if(!seen[id]){ panel.bgLayer.removeLayer(pool[id]); delete pool[id]; } }
  // signal lights: recolour by approximated state; highlight the next light ahead
  let nextS=Infinity,nextEl=null;
  panel.tlMarkers.forEach(tl=>{
    const el=tl.el.getElement(); if(!el) return;
    el.dataset.state=stateAt(tl,t);
    el.classList.remove("next");
    if(tl.s!=null&&tl.s>st.dist_m&&tl.s<nextS){nextS=tl.s;nextEl=el;}
  });
  if(nextEl) nextEl.classList.add("next");
  // readouts
  document.getElementById(mode==="B0"?"spdB0":"spdB2").textContent=arrived?"도착":st.speed_kmh.toFixed(0);
  document.getElementById(mode==="B0"?"progB0":"progB2").textContent=
    Math.min(100,Math.round(st.dist_m/panel.routeLen*100));
  document.getElementById(mode==="B0"?"nsB0":"nsB2").textContent=
    nextEl?(SIG_LABEL[nextEl.dataset.state]||"–"):"–";
}

// overview minimap
function buildOverview(){
  const m=makeMap("mapOv");
  const all=[];
  ["B0","B2"].forEach(k=>{const pl=DATA.modes[k].route_polyline;
    L.polyline(pl,{color:COLORS[k],weight:2,opacity:.6}).addTo(m);all.push(...pl);});
  // traffic-light positions for corridor context (static neutral dots)
  (DATA.traffic_lights||[]).forEach(t=>L.circleMarker([t.lat,t.lon],
    {radius:2.5,weight:0,fillColor:"#64748b",fillOpacity:.6}).addTo(m));
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


def build_animated_dual_map_html(
    doc: dict[str, Any], output_path: Path, title: str, basemap: str = "carto_light"
) -> None:
    """Render the animation JSON document to a standalone HTML file.

    ``basemap``: ``carto_light`` (clean light map WITH road/district labels, no
    shop POIs/buildings — default), ``carto_light_nolabels`` (same, no labels),
    ``carto_dark``, ``osm`` (cluttered), ``none`` (solid canvas, SUMO geometry only).
    """
    html = (
        _TEMPLATE
        .replace("__DATA__", json.dumps(doc, ensure_ascii=False))
        .replace("__TILES__", MAP_TILES)
        .replace("__ATTR__", MAP_ATTRIBUTION)
        .replace("__B0COLOR__", MODE_COLORS["B0"])
        .replace("__B2COLOR__", MODE_COLORS["B2"])
        .replace("__BASEMAP__", basemap)
        .replace("__TITLE__", title)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
