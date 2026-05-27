"""HTML helpers for manual map review tools."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError(f"ERROR: JSON root must be an object: {path}")
    return payload


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def json_for_inline_script(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def html_escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def build_selected_edges_schema() -> dict[str, Any]:
    edge_array = {
        "type": "array",
        "items": {"type": "string"},
        "uniqueItems": True,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Step 4 selected edge lists",
        "type": "object",
        "required": [
            "analysis_edges",
            "accident_candidate_edges",
            "excluded_edges",
            "created_from",
            "notes",
        ],
        "properties": {
            "analysis_edges": edge_array,
            "accident_candidate_edges": edge_array,
            "excluded_edges": edge_array,
            "created_from": {"type": "string"},
            "notes": {"type": "string"},
        },
        "additionalProperties": False,
    }


def render_map_review_html(context: dict[str, Any]) -> str:
    context_json = json_for_inline_script(context)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SUMO Map Review</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIINfQBdjlIeqio5I8fAFs1C7lYLVQ2wZj4=" crossorigin="">
  <style>
    .leaflet-container {{ overflow: hidden; }}
    .leaflet-pane,
    .leaflet-tile,
    .leaflet-marker-icon,
    .leaflet-marker-shadow,
    .leaflet-tile-container,
    .leaflet-pane > svg,
    .leaflet-pane > canvas,
    .leaflet-zoom-box,
    .leaflet-image-layer,
    .leaflet-layer {{
      position: absolute;
      left: 0;
      top: 0;
    }}
    .leaflet-container img {{
      max-width: none !important;
      max-height: none !important;
    }}
    .leaflet-tile {{
      width: 256px;
      height: 256px;
    }}
    :root {{
      --bg: #f7f8fb;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #667085;
      --line: #d7dce5;
      --blue: #2563eb;
      --green: #12805c;
      --red: #c2410c;
      --amber: #b45309;
      --purple: #7c3aed;
    }}
    * {{ box-sizing: border-box; }}
    html,
    body {{
      height: 100%;
      margin: 0;
    }}
    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
    }}
    .app {{
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }}
    header {{
      flex: 0 0 56px;
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      min-width: 0;
    }}
    .content {{
      flex: 1 1 auto;
      min-height: 0;
      display: flex;
      overflow: hidden;
    }}
    .map-wrap {{
      flex: 1 1 auto;
      min-width: 0;
      min-height: 0;
      position: relative;
    }}
    h1 {{
      margin: 0;
      font-size: 17px;
      line-height: 1.2;
      white-space: nowrap;
    }}
    .toolbar {{
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      min-width: 0;
    }}
    .segmented {{
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow: hidden;
      background: #fff;
    }}
    .segmented label {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      min-height: 34px;
      padding: 0 10px;
      font-size: 13px;
      border-right: 1px solid var(--line);
      cursor: pointer;
      white-space: nowrap;
    }}
    .segmented label:last-child {{ border-right: 0; }}
    .segmented input {{ margin: 0; }}
    .toggle {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 13px;
      white-space: nowrap;
    }}
    button {{
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      color: var(--text);
      font-size: 13px;
      cursor: pointer;
    }}
    button.primary {{
      border-color: var(--blue);
      background: var(--blue);
      color: #fff;
    }}
    #map {{
      width: 100%;
      height: 100%;
      min-height: 0;
    }}
    aside {{
      flex: 0 0 380px;
      width: 380px;
      position: relative;
      overflow: auto;
      border-left: 1px solid var(--line);
      background: var(--panel);
      padding: 14px;
    }}
    .section {{
      padding: 0 0 14px;
      margin: 0 0 14px;
      border-bottom: 1px solid var(--line);
    }}
    .section:last-child {{ border-bottom: 0; }}
    h2 {{
      margin: 0 0 8px;
      font-size: 14px;
      line-height: 1.25;
    }}
    .muted {{ color: var(--muted); font-size: 12px; line-height: 1.45; }}
    .status {{
      font-size: 12px;
      line-height: 1.45;
      color: var(--muted);
    }}
    .warning {{
      margin-top: 8px;
      padding: 8px;
      border-left: 3px solid var(--amber);
      background: #fff7ed;
      color: #7c2d12;
      font-size: 12px;
      line-height: 1.45;
    }}
    dl {{
      display: grid;
      grid-template-columns: 130px minmax(0, 1fr);
      gap: 6px 8px;
      margin: 0;
      font-size: 12px;
    }}
    dt {{ color: var(--muted); }}
    dd {{
      margin: 0;
      overflow-wrap: anywhere;
    }}
    .counts {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 6px 10px;
      font-size: 13px;
    }}
    .list {{
      max-height: 140px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      font: 12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      background: #fbfcfe;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
    .preview {{
      max-height: 260px;
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      font: 12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      background: #fbfcfe;
      white-space: pre-wrap;
    }}
    @media (max-width: 900px) {{
      .app {{
        height: auto;
        min-height: 100vh;
      }}
      header {{ flex: 0 0 auto; align-items: flex-start; flex-direction: column; }}
      .content {{ flex-direction: column; min-height: 0; }}
      .map-wrap {{ flex: 0 0 60vh; min-height: 420px; }}
      aside {{ flex: 0 0 auto; width: 100%; border-left: 0; border-top: 1px solid var(--line); }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <header>
      <h1>SUMO Map Review</h1>
      <div class="toolbar">
        <div class="segmented" role="radiogroup" aria-label="selection mode">
          <label><input type="radio" name="mode" value="analysis_edge" checked> 분석 edge</label>
          <label><input type="radio" name="mode" value="accident_candidate_edge"> 사고 후보 edge</label>
          <label><input type="radio" name="mode" value="excluded_edge"> 제외 edge</label>
        </div>
        <label class="toggle"><input id="toggle-analysis" type="checkbox"> 분석권역</label>
        <label class="toggle"><input id="toggle-edges" type="checkbox"> SUMO edge</label>
        <label class="toggle"><input id="toggle-tls" type="checkbox"> 신호등</label>
        <button id="fit-analysis" type="button">분석권역으로 이동</button>
        <button id="fit-first-edge" type="button">첫 edge로 이동</button>
        <button id="fit-first10-edges" type="button">first 10 edges로 이동</button>
        <button id="fit-edges" type="button">edge 영역으로 이동</button>
        <button id="fit-tls" type="button">신호등 영역으로 이동</button>
        <button class="primary" id="download-json" type="button">선택 결과 다운로드</button>
        <button id="clear-selection" type="button">선택 초기화</button>
      </div>
    </header>
    <div class="content">
      <main class="map-wrap"><div id="map"></div></main>
      <aside>
        <section class="section">
          <h2>상태</h2>
          <div id="status" class="status">Loading layers...</div>
          <p class="muted">file://로 열면 GeoJSON fetch가 막힐 수 있음. 기본 실행은 <code>cd /Users/junlee/Desktop/js</code>, <code>python3 -m http.server 8000</code>, <code>http://localhost:8000/results/html/map_review.html</code>.</p>
        </section>
        <section class="section">
          <h2>클릭한 Edge</h2>
          <div id="edge-warning"></div>
          <dl id="edge-props"><dt>edge_id</dt><dd>none</dd></dl>
        </section>
        <section class="section">
          <h2>선택 개수</h2>
          <div class="counts">
            <span>analysis_edges</span><strong id="count-analysis">0</strong>
            <span>accident_candidate_edges</span><strong id="count-accident">0</strong>
            <span>excluded_edges</span><strong id="count-excluded">0</strong>
          </div>
        </section>
        <section class="section">
          <h2>선택된 Edge ID</h2>
          <div id="selection-list" class="list"></div>
        </section>
        <section class="section">
          <h2>다운로드 미리보기</h2>
          <div id="download-preview" class="preview"></div>
        </section>
      </aside>
    </div>
  </div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <script>
    const APP_CONTEXT = {context_json};
    const selected = {{
      analysis_edges: new Set(),
      accident_candidate_edges: new Set(),
      excluded_edges: new Set(),
    }};
    let currentMode = 'analysis_edge';
    let edgeLayer = null;
    let tlsLayer = null;
    let analysisLayer = null;
    let debugAnalysisLayer = null;
    let debugFirstEdgeMarker = null;
    let debugFirst10EdgesLayer = null;
    let debugTls10Layer = null;
    let selectedEdgeLayer = new Map();
    const DEFAULT_LAYER_VISIBILITY = {{
      analysis: true,
      edges: true,
      tls: false,
    }};
    const loadState = {{
      analysis: 0,
      edges: 0,
      tls: 0,
      analysisVisible: false,
      edgesVisible: false,
      tlsVisible: false,
      analysisBounds: 'not loaded',
      edgeBounds: 'not loaded',
      tlsBounds: 'not loaded',
      first10EdgeBounds: 'not loaded',
      firstEdgeCoordinateSample: 'not loaded',
      firstEdgeId: 'not loaded',
      firstEdgeMarkerLatLon: 'not loaded',
      debugFirstEdgeMarkerVisible: false,
      debugFirst10EdgesVisible: false,
      debugTls10Visible: false,
      lastClickedEdgeId: 'none',
      lastError: 'none',
      center: APP_CONTEXT.initial_center,
      zoom: APP_CONTEXT.initial_zoom,
      leafletTilePosition: 'not checked',
    }};

    const modeToKey = {{
      analysis_edge: 'analysis_edges',
      accident_candidate_edge: 'accident_candidate_edges',
      excluded_edge: 'excluded_edges',
    }};
    const modeColors = {{
      analysis_edges: '#0057ff',
      accident_candidate_edges: '#dc2626',
      excluded_edges: '#111827',
    }};

    function setStatus(message) {{
      document.getElementById('status').textContent = [
        message,
        `분석권역 로드 개수: ${{loadState.analysis}}`,
        `edge 로드 개수: ${{loadState.edges}}`,
        `TLS 로드 개수: ${{loadState.tls}}`,
        `분석권역 표시 여부: ${{loadState.analysisVisible}}`,
        `SUMO edge 표시 여부: ${{loadState.edgesVisible}}`,
        `신호등 표시 여부: ${{loadState.tlsVisible}}`,
        `현재 지도 중심/줌: ${{formatCenterZoom()}}`,
        `analysis bounds: ${{loadState.analysisBounds}}`,
        `first 10 edge bounds: ${{loadState.first10EdgeBounds}}`,
        `edge bounds: ${{loadState.edgeBounds}}`,
        `TLS bounds: ${{loadState.tlsBounds}}`,
        `first edge id: ${{loadState.firstEdgeId}}`,
        `첫 번째 edge 좌표 샘플: ${{loadState.firstEdgeCoordinateSample}}`,
        `first edge marker lat/lon: ${{loadState.firstEdgeMarkerLatLon}}`,
        `마지막 클릭 edge_id: ${{loadState.lastClickedEdgeId}}`,
        `debug first edge marker 표시 여부: ${{loadState.debugFirstEdgeMarkerVisible}}`,
        `map.hasLayer(debugEdgeLayer): ${{loadState.debugFirst10EdgesVisible}}`,
        `debug TLS 10 표시 여부: ${{loadState.debugTls10Visible}}`,
        `leaflet tile position: ${{loadState.leafletTilePosition}}`,
        `마지막 오류 메시지: ${{loadState.lastError}}`,
      ].join('\\n');
    }}

    function formatCenterZoom() {{
      if (!loadState.center) return 'unknown';
      const lat = Number(loadState.center[0]).toFixed(6);
      const lon = Number(loadState.center[1]).toFixed(6);
      return `${{lat}}, ${{lon}} / ${{loadState.zoom}}`;
    }}

    function updateMapState(map) {{
      const center = map.getCenter();
      loadState.center = [center.lat, center.lng];
      loadState.zoom = map.getZoom();
      updateLayerDebugState(map);
    }}

    function boundsText(layer) {{
      if (!layer || typeof layer.getBounds !== 'function') return 'not available';
      const bounds = layer.getBounds();
      if (!bounds || !bounds.isValid()) return 'invalid';
      const sw = bounds.getSouthWest();
      const ne = bounds.getNorthEast();
      return `SW(${{sw.lat.toFixed(6)}}, ${{sw.lng.toFixed(6)}}) NE(${{ne.lat.toFixed(6)}}, ${{ne.lng.toFixed(6)}})`;
    }}

    function updateLayerDebugState(map) {{
      loadState.analysisVisible = !!(analysisLayer && map.hasLayer(analysisLayer));
      loadState.edgesVisible = !!(edgeLayer && map.hasLayer(edgeLayer));
      loadState.tlsVisible = !!(tlsLayer && map.hasLayer(tlsLayer));
      loadState.debugFirstEdgeMarkerVisible = !!(debugFirstEdgeMarker && map.hasLayer(debugFirstEdgeMarker));
      loadState.debugFirst10EdgesVisible = !!(debugFirst10EdgesLayer && map.hasLayer(debugFirst10EdgesLayer));
      loadState.debugTls10Visible = !!(debugTls10Layer && map.hasLayer(debugTls10Layer));
      loadState.analysisBounds = boundsText(analysisLayer);
      loadState.edgeBounds = boundsText(edgeLayer);
      loadState.tlsBounds = boundsText(tlsLayer);
      loadState.first10EdgeBounds = boundsText(debugFirst10EdgesLayer);
    }}

    function setLayerVisible(map, layer, visible) {{
      if (!layer) return;
      if (visible && !map.hasLayer(layer)) map.addLayer(layer);
      if (!visible && map.hasLayer(layer)) map.removeLayer(layer);
      updateMapState(map);
      syncLayerVisibility(map);
    }}

    function syncLayerVisibility(map) {{
      const analysisToggle = document.getElementById('toggle-analysis');
      const edgeToggle = document.getElementById('toggle-edges');
      const tlsToggle = document.getElementById('toggle-tls');
      if (analysisToggle) analysisToggle.checked = !!(analysisLayer && map.hasLayer(analysisLayer));
      if (edgeToggle) edgeToggle.checked = !!(edgeLayer && map.hasLayer(edgeLayer));
      if (tlsToggle) tlsToggle.checked = !!(tlsLayer && map.hasLayer(tlsLayer));
      updateLayerDebugState(map);
    }}

    function fitLayer(map, layer, label, showLayer = false) {{
      if (!layer || typeof layer.getBounds !== 'function') {{
        loadState.lastError = `${{label}} layer가 아직 로드되지 않았습니다.`;
        setStatus(`${{label}} 이동 실패`);
        return;
      }}
      if (showLayer && !map.hasLayer(layer)) {{
        map.addLayer(layer);
        syncLayerVisibility(map);
      }}
      const bounds = layer.getBounds();
      if (!bounds || !bounds.isValid()) {{
        loadState.lastError = `${{label}} bounds가 유효하지 않습니다.`;
        setStatus(`${{label}} 이동 실패`);
        return;
      }}
      map.fitBounds(bounds.pad(0.05));
      updateMapState(map);
      setStatus(`${{label}} bounds로 이동했습니다.`);
    }}

    function fitFirstEdge(map) {{
      if (!debugFirstEdgeMarker) {{
        loadState.lastError = '첫 edge marker가 아직 생성되지 않았습니다.';
        setStatus('첫 edge 이동 실패');
        return;
      }}
      if (!map.hasLayer(debugFirstEdgeMarker)) map.addLayer(debugFirstEdgeMarker);
      const latlng = debugFirstEdgeMarker.getLatLng();
      map.setView(latlng, 18);
      updateMapState(map);
      setStatus('첫 edge 위치로 이동했습니다.');
    }}

    function logDiagnostics(label, extra = {{}}) {{
      const payload = {{
        label,
        analysisFeatureCount: loadState.analysis,
        edgeFeatureCount: loadState.edges,
        tlsFeatureCount: loadState.tls,
        firstEdgeId: loadState.firstEdgeId,
        firstEdgeCoordinateSample: loadState.firstEdgeCoordinateSample,
        firstEdgeMarkerLatLon: loadState.firstEdgeMarkerLatLon,
        analysisBounds: loadState.analysisBounds,
        first10EdgeBounds: loadState.first10EdgeBounds,
        edgeBounds: loadState.edgeBounds,
        tlsBounds: loadState.tlsBounds,
        hasAnalysisLayer: loadState.analysisVisible,
        hasEdgeLayer: loadState.edgesVisible,
        hasTlsLayer: loadState.tlsVisible,
        hasDebugEdgeLayer: loadState.debugFirst10EdgesVisible,
        centerZoom: formatCenterZoom(),
        lastError: loadState.lastError,
        ...extra,
      }};
      console.log('Step4 map review diagnostics', payload);
    }}

    function diagnoseLeafletCss(map, label) {{
      const tile = document.querySelector('.leaflet-tile');
      if (!tile) {{
        loadState.leafletTilePosition = 'tile not created yet';
        setStatus(`${{label}}. Waiting for tile element...`);
        return;
      }}
      const position = getComputedStyle(tile).position;
      loadState.leafletTilePosition = position;
      if (position !== 'absolute') {{
        loadState.lastError = 'Leaflet CSS may not be loaded';
      }}
      updateMapState(map);
      setStatus(label);
    }}

    function relFetch(path) {{
      return fetch(path).then(response => {{
        if (!response.ok) throw new Error(`${{response.status}} ${{response.statusText}}`);
        return response.json();
      }});
    }}

    function edgeBaseStyle(feature) {{
      const p = feature.properties || {{}};
      if (p.is_internal) {{
        return {{ color: '#7c3aed', weight: 2, opacity: 0.55 }};
      }}
      if (!p.allows_passenger) {{
        return {{ color: '#f97316', weight: 2.5, opacity: 0.9 }};
      }}
      return {{ color: '#0f5bff', weight: 2.2, opacity: 0.82 }};
    }}

    function selectedStyle(edgeId, feature) {{
      const key = findSelectionKey(edgeId);
      if (!key) return edgeBaseStyle(feature);
      return {{ color: modeColors[key], weight: 5, opacity: 1.0 }};
    }}

    function analysisStyle(feature) {{
      const role = feature.properties && feature.properties.role;
      if (role === 'analysis_ellipse') return {{ color: '#0057ff', weight: 5, fillColor: '#60a5fa', fillOpacity: 0.16 }};
      if (role === 'osm_extract_bbox') return {{ color: '#111827', weight: 3, dashArray: '7 5', fillOpacity: 0.03 }};
      if (role === 'analysis_axis') return {{ color: '#7c3aed', weight: 5, opacity: 0.95 }};
      return {{ color: '#0057ff', weight: 3, fillOpacity: 0.95 }};
    }}

    function tlsPoint(feature, latlng) {{
      return L.circleMarker(latlng, {{
        radius: 6,
        color: '#b91c1c',
        fillColor: '#f97316',
        fillOpacity: 0.92,
        weight: 2,
      }});
    }}

    function debugTlsPoint(feature, latlng) {{
      return L.circleMarker(latlng, {{
        radius: 10,
        color: '#7f1d1d',
        fillColor: '#ef4444',
        fillOpacity: 0.95,
        weight: 3,
      }});
    }}

    function debugAnalysisStyle(feature) {{
      const role = feature.properties && feature.properties.role;
      if (role === 'analysis_axis') return {{ color: '#000000', weight: 7, opacity: 1 }};
      if (role === 'osm_extract_bbox') return {{ color: '#111827', weight: 5, dashArray: '8 4', fillOpacity: 0.04 }};
      if (role === 'analysis_ellipse') return {{ color: '#1d4ed8', weight: 7, fillColor: '#93c5fd', fillOpacity: 0.22 }};
      return {{ color: '#1d4ed8', weight: 4, fillOpacity: 1 }};
    }}

    function debugFirst10Style() {{
      return {{ color: '#ff1493', weight: 7, opacity: 1 }};
    }}

    function findPreferredAnalysisBounds(analysis) {{
      const bboxFeature = (analysis.features || []).find(feature => {{
        return feature.properties && feature.properties.role === 'osm_extract_bbox';
      }});
      if (bboxFeature) {{
        const layer = L.geoJSON(bboxFeature);
        const bounds = layer.getBounds();
        if (bounds.isValid()) return bounds;
      }}
      if (analysisLayer) {{
        const bounds = analysisLayer.getBounds();
        if (bounds.isValid()) return bounds;
      }}
      return null;
    }}

    function firstEdgeLatLon(edges) {{
      const first = edges.features && edges.features[0];
      if (!first || !first.geometry || !first.geometry.coordinates) return null;
      const coord = first.geometry.coordinates[0];
      if (!coord || coord.length < 2) return null;
      return {{ lat: Number(coord[1]), lon: Number(coord[0]) }};
    }}

    function featureCollectionFromFirst(features, count) {{
      return {{
        type: 'FeatureCollection',
        features: (features || []).slice(0, count),
      }};
    }}

    function findSelectionKey(edgeId) {{
      for (const key of Object.keys(selected)) {{
        if (selected[key].has(edgeId)) return key;
      }}
      return null;
    }}

    function removeFromAll(edgeId) {{
      for (const key of Object.keys(selected)) selected[key].delete(edgeId);
    }}

    function toggleEdge(feature, layer) {{
      const props = feature.properties || {{}};
      const edgeId = props.edge_id;
      if (!edgeId) return;
      const targetKey = modeToKey[currentMode];
      const currentKey = findSelectionKey(edgeId);
      if (currentKey === targetKey) {{
        selected[targetKey].delete(edgeId);
      }} else {{
        removeFromAll(edgeId);
        selected[targetKey].add(edgeId);
      }}
      selectedEdgeLayer.set(edgeId, {{ layer, feature }});
      layer.setStyle(selectedStyle(edgeId, feature));
      loadState.lastClickedEdgeId = edgeId;
      showEdge(props);
      renderSelection();
      setStatus(`edge 선택 갱신: ${{edgeId}}`);
    }}

    function showEdge(props) {{
      const keys = ['edge_id', 'length_m', 'speed_mps', 'lane_count', 'is_internal', 'allows_passenger', 'allows_emergency_candidate', 'from_node', 'to_node'];
      const html = keys.map(key => `<dt>${{key}}</dt><dd>${{props[key] ?? ''}}</dd>`).join('');
      document.getElementById('edge-props').innerHTML = html;
      const warnings = [];
      if (props.is_internal) warnings.push('경고: SUMO internal edge입니다.');
      if (currentMode === 'accident_candidate_edge' && props.allows_passenger === false) {{
        warnings.push('경고: passenger 차량이 허용되지 않는 edge입니다.');
      }}
      document.getElementById('edge-warning').innerHTML = warnings.map(text => `<div class="warning">${{text}}</div>`).join('');
    }}

    function selectedPayload() {{
      return {{
        analysis_edges: Array.from(selected.analysis_edges).sort(),
        accident_candidate_edges: Array.from(selected.accident_candidate_edges).sort(),
        excluded_edges: Array.from(selected.excluded_edges).sort(),
        created_from: 'results/html/map_review.html',
        notes: 'manual edge selection from Step 4',
      }};
    }}

    function renderSelection() {{
      const payload = selectedPayload();
      document.getElementById('count-analysis').textContent = payload.analysis_edges.length;
      document.getElementById('count-accident').textContent = payload.accident_candidate_edges.length;
      document.getElementById('count-excluded').textContent = payload.excluded_edges.length;
      document.getElementById('selection-list').textContent = [
        `analysis_edges (${{payload.analysis_edges.length}}):`,
        payload.analysis_edges.join('\\n') || '-',
        '',
        `accident_candidate_edges (${{payload.accident_candidate_edges.length}}):`,
        payload.accident_candidate_edges.join('\\n') || '-',
        '',
        `excluded_edges (${{payload.excluded_edges.length}}):`,
        payload.excluded_edges.join('\\n') || '-',
      ].join('\\n');
      document.getElementById('download-preview').textContent = JSON.stringify(payload, null, 2);
    }}

    function downloadSelection() {{
      const blob = new Blob([JSON.stringify(selectedPayload(), null, 2) + '\\n'], {{ type: 'application/json' }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'selected_edges.json';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }}

    function clearSelection() {{
      for (const key of Object.keys(selected)) selected[key].clear();
      for (const item of selectedEdgeLayer.values()) {{
        item.layer.setStyle(edgeBaseStyle(item.feature));
      }}
      selectedEdgeLayer.clear();
      renderSelection();
    }}

    function invalidateSoon(map) {{
      map.invalidateSize();
      setTimeout(() => map.invalidateSize(), 300);
    }}

    async function init() {{
      const map = L.map('map', {{
        preferCanvas: true,
        zoomControl: true,
        attributionControl: true,
      }}).setView(APP_CONTEXT.initial_center, APP_CONTEXT.initial_zoom);
      L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
        maxZoom: 20,
        attribution: '&copy; OpenStreetMap contributors',
      }}).addTo(map);
      updateMapState(map);
      setStatus('Base map initialized. Loading analysis area...');
      map.invalidateSize();
      setTimeout(() => map.invalidateSize(), 300);
      setTimeout(() => diagnoseLeafletCss(map, 'Leaflet CSS diagnostic after tile init.'), 300);
      map.on('moveend zoomend', () => {{
        updateMapState(map);
        setStatus('지도 위치가 변경되었습니다.');
      }});

      document.getElementById('toggle-analysis').checked = DEFAULT_LAYER_VISIBILITY.analysis;
      document.getElementById('toggle-edges').checked = DEFAULT_LAYER_VISIBILITY.edges;
      document.getElementById('toggle-tls').checked = DEFAULT_LAYER_VISIBILITY.tls;

      document.querySelectorAll('input[name="mode"]').forEach(input => {{
        input.addEventListener('change', event => {{
          currentMode = event.target.value;
        }});
      }});
      document.getElementById('download-json').addEventListener('click', downloadSelection);
      document.getElementById('clear-selection').addEventListener('click', clearSelection);
      document.getElementById('fit-analysis').addEventListener('click', () => fitLayer(map, analysisLayer, '분석권역', true));
      document.getElementById('fit-first-edge').addEventListener('click', () => fitFirstEdge(map));
      document.getElementById('fit-first10-edges').addEventListener('click', () => fitLayer(map, debugFirst10EdgesLayer, 'first 10 edges', true));
      document.getElementById('fit-edges').addEventListener('click', () => fitLayer(map, edgeLayer, 'edge 영역', true));
      document.getElementById('fit-tls').addEventListener('click', () => fitLayer(map, tlsLayer, '신호등 영역', true));

      try {{
        const analysis = await relFetch(APP_CONTEXT.paths.analysis_area_geojson);
        analysisLayer = L.geoJSON(analysis, {{
          style: analysisStyle,
          pointToLayer: (feature, latlng) => L.circleMarker(latlng, {{ radius: 7, color: '#2563eb', fillColor: '#ffffff', fillOpacity: 1, weight: 2 }}),
          onEachFeature: (feature, layer) => {{
            const p = feature.properties || {{}};
            layer.bindTooltip(p.name || p.role || 'analysis area');
          }},
        }});
        debugAnalysisLayer = L.geoJSON(analysis, {{
          style: debugAnalysisStyle,
          pointToLayer: (feature, latlng) => L.circleMarker(latlng, {{ radius: 10, color: '#000000', fillColor: '#facc15', fillOpacity: 0.95, weight: 3 }}),
        }});
        setLayerVisible(map, analysisLayer, DEFAULT_LAYER_VISIBILITY.analysis);
        if (!map.hasLayer(debugAnalysisLayer)) map.addLayer(debugAnalysisLayer);
        loadState.analysis = analysis.features.length;
        const bounds = findPreferredAnalysisBounds(analysis);
        if (bounds && bounds.isValid()) {{
          map.fitBounds(bounds.pad(0.08));
        }} else {{
          map.setView(APP_CONTEXT.initial_center, APP_CONTEXT.initial_zoom);
        }}
        updateMapState(map);
        invalidateSoon(map);
        diagnoseLeafletCss(map, 'Analysis area loaded. Loading edge layer...');
        logDiagnostics('analysis loaded', {{ analysisRoles: analysis.features.map(feature => feature.properties && feature.properties.role) }});
        setStatus('분석권역 로드 완료. SUMO edge 레이어 로드 중...');

        await new Promise(resolve => requestAnimationFrame(resolve));
        const edges = await relFetch(APP_CONTEXT.paths.sumo_edges_geojson);
        if (edges.features && edges.features.length > 0) {{
          loadState.firstEdgeId = edges.features[0].properties && edges.features[0].properties.edge_id;
          const coords = edges.features[0].geometry && edges.features[0].geometry.coordinates;
          loadState.firstEdgeCoordinateSample = JSON.stringify(coords ? coords.slice(0, 2) : null);
          const firstLatLon = firstEdgeLatLon(edges);
          if (firstLatLon) {{
            loadState.firstEdgeMarkerLatLon = `${{firstLatLon.lat.toFixed(6)}}, ${{firstLatLon.lon.toFixed(6)}}`;
            debugFirstEdgeMarker = L.circleMarker([firstLatLon.lat, firstLatLon.lon], {{
              radius: 14,
              color: '#000000',
              fillColor: '#facc15',
              fillOpacity: 1,
              weight: 4,
            }}).bindTooltip(`첫 edge: ${{loadState.firstEdgeId}}`, {{ sticky: true }});
            debugFirstEdgeMarker.addTo(map);
          }}
        }}
        edgeLayer = L.geoJSON(edges, {{
          style: edgeBaseStyle,
          onEachFeature: (feature, layer) => {{
            const p = feature.properties || {{}};
            layer.on('click', () => toggleEdge(feature, layer));
            layer.bindTooltip(p.edge_id || '', {{ sticky: true }});
          }},
        }});
        debugFirst10EdgesLayer = L.geoJSON(featureCollectionFromFirst(edges.features, 10), {{
          style: debugFirst10Style,
          onEachFeature: (feature, layer) => {{
            const p = feature.properties || {{}};
            layer.bindTooltip(`debug first10: ${{p.edge_id || ''}}`, {{ sticky: true }});
          }},
        }}).addTo(map);
        setLayerVisible(map, edgeLayer, DEFAULT_LAYER_VISIBILITY.edges);
        loadState.edges = edges.features.length;
        map.invalidateSize();
        diagnoseLeafletCss(map, 'Edge layer loaded. Loading TLS layer metadata...');
        logDiagnostics('edges loaded', {{
          firstEdgeProperties: edges.features[0] && edges.features[0].properties,
          firstEdgeCoordinates: edges.features[0] && edges.features[0].geometry && edges.features[0].geometry.coordinates && edges.features[0].geometry.coordinates.slice(0, 2),
        }});
        setStatus('SUMO edge 로드 완료. 신호등 레이어 로드 중...');

        await new Promise(resolve => setTimeout(resolve, 0));
        const tls = await relFetch(APP_CONTEXT.paths.sumo_tls_geojson);
        tlsLayer = L.geoJSON(tls, {{
          pointToLayer: tlsPoint,
          onEachFeature: (feature, layer) => {{
            const p = feature.properties || {{}};
            layer.bindTooltip(`${{p.tls_id || ''}} links=${{p.controlled_link_count ?? 0}}`, {{ sticky: true }});
          }},
        }});
        debugTls10Layer = L.geoJSON(featureCollectionFromFirst(tls.features, 10), {{
          pointToLayer: debugTlsPoint,
          onEachFeature: (feature, layer) => {{
            const p = feature.properties || {{}};
            layer.bindTooltip(`debug TLS: ${{p.tls_id || ''}}`, {{ sticky: true }});
          }},
        }}).addTo(map);
        loadState.tls = tls.features.length;
        setLayerVisible(map, tlsLayer, DEFAULT_LAYER_VISIBILITY.tls);

        document.getElementById('toggle-analysis').addEventListener('change', event => {{
          setLayerVisible(map, analysisLayer, event.target.checked);
          setStatus('분석권역 표시 상태가 변경되었습니다.');
        }});
        document.getElementById('toggle-edges').addEventListener('change', event => {{
          setLayerVisible(map, edgeLayer, event.target.checked);
          setStatus('SUMO edge 표시 상태가 변경되었습니다.');
        }});
        document.getElementById('toggle-tls').addEventListener('change', event => {{
          setLayerVisible(map, tlsLayer, event.target.checked);
          setStatus('신호등 표시 상태가 변경되었습니다.');
        }});

        updateMapState(map);
        syncLayerVisibility(map);
        invalidateSoon(map);
        diagnoseLeafletCss(map, '모든 레이어 로드 완료. 신호등은 기본 OFF입니다.');
        logDiagnostics('all layers loaded', {{
          firstTlsProperties: tls.features[0] && tls.features[0].properties,
          firstTlsCoordinates: tls.features[0] && tls.features[0].geometry && tls.features[0].geometry.coordinates,
        }});
        setStatus('모든 레이어 로드 완료. 신호등은 기본 OFF입니다.');
      }} catch (error) {{
        loadState.lastError = error.message;
        setStatus('레이어 로드 실패. file:// 대신 localhost 실행을 확인하세요.');
      }}
      renderSelection();
    }}

    document.addEventListener('DOMContentLoaded', init);
  </script>
</body>
</html>
"""
