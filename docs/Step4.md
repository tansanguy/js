# Step 4 Map Review HTML

## 목표

Step 4는 실제 지도 위에 SUMO edge, traffic light, 분석권역을 겹쳐 보여주는 `map_review.html`을 생성하는 단계다.

사용자는 이 HTML에서 edge를 클릭해 분석 edge, 사고 후보 edge, 제외 edge를 수동 선택하고, 선택 결과를 `selected_edges.json`으로 다운로드한다.

## 입력

- `data_prepared/geojson/analysis_area.geojson`
- `data_prepared/geojson/sumo_edges.geojson`
- `data_prepared/geojson/sumo_tls.geojson`
- `data_prepared/geojson/step03_geojson_audit.json`
- `config/map_config.yaml`

## 출력

- `results/html/map_review.html`
- `data_prepared/manual/selected_edges.schema.json`
- `outputs/logs/step04_make_map_review_html.log`

## 실제 수행 결과

실행한 명령:

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/01_map/step04_make_map_review_html.py
```

실행 결과:

```text
Status: PASS
analysis_feature_count: 5
edge_feature_count: 21599
tls_feature_count: 356
Wrote HTML: results/html/map_review.html
Wrote schema: data_prepared/manual/selected_edges.schema.json
```

생성된 파일:

- `results/html/map_review.html` 약 16 KB
- `data_prepared/manual/selected_edges.schema.json` 약 809 B
- `outputs/logs/step04_make_map_review_html.log` 약 472 B

검증 결과:

- Step 0 환경 확인 PASS.
- `selected_edges.schema.json` JSON valid.
- HTML 안에 Leaflet map, selection mode 3개, JSON download handler, CORS fallback 안내 포함 확인.
- HTML에서 참조하는 GeoJSON 경로:
  - `../../data_prepared/geojson/analysis_area.geojson`
  - `../../data_prepared/geojson/sumo_edges.geojson`
  - `../../data_prepared/geojson/sumo_tls.geojson`

## 구현 원칙

- Leaflet 기반 단일 HTML 파일을 생성한다.
- GeoJSON은 외부 파일로 로드한다.
- 로컬 `file://`에서 fetch가 막히면 프로젝트 루트에서 `python3 -m http.server 8000`을 실행한다.
- 선택 결과는 브라우저 다운로드로만 만든다.
- `data_prepared/manual/selected_edges.json`은 자동 생성하지 않는다.
- 사고 시나리오, route, demand, controller, simulation은 만들지 않는다.

## 선택 모드

- `analysis_edge`: 분석 대상 도로망 edge
- `accident_candidate_edge`: 사고 발생 후보 edge
- `excluded_edge`: 제외 edge

## 선택 결과 JSON

```json
{
  "analysis_edges": ["edge_id_1", "edge_id_2"],
  "accident_candidate_edges": ["edge_id_3"],
  "excluded_edges": ["edge_id_4"],
  "created_from": "results/html/map_review.html",
  "notes": "manual edge selection from Step 4"
}
```

## 성능 메모

`sumo_edges.geojson`은 약 17MB이며 브라우저 렌더링이 몇 초 걸릴 수 있다.

v1 대응:

- Leaflet `preferCanvas: true`
- 단순 line style
- TLS layer 기본 off
- edge hover 최소화
- 선택 edge만 굵게 표시

## 실행 명령

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/01_map/step04_make_map_review_html.py
```

## 브라우저 확인

권장 방식은 localhost 서버다. `file://` 직접 열기는 브라우저 CORS 정책 때문에 GeoJSON fetch가 막힐 수 있다.

```bash
cd /Users/junlee/Desktop/js
python3 -m http.server 8000
```

브라우저에서 열기:

```text
http://localhost:8000/results/html/map_review.html
```

`open results/html/map_review.html`도 가능하지만, 레이어가 로드되지 않으면 localhost 방식으로 확인한다.

## Layout Fix 기록

지도 타일이 부분적으로만 렌더링되고 흰 영역이 생기는 문제를 줄이기 위해 Leaflet container layout을 안정화했다.

수정 내용:

- `html`, `body` height를 `100%`로 고정.
- `.app`을 `100vh` flex column으로 변경.
- toolbar는 고정 높이, 본문은 flex row로 분리.
- map 영역은 `flex: 1`, sidebar는 고정 width.
- `#map`은 `width: 100%`, `height: 100%`.
- Leaflet 초기화 후 `map.invalidateSize()` 호출.
- analysis area 로드/fitBounds 후 `invalidateSize()` 호출.
- edge layer 로드 후 `invalidateSize()` 호출.
- 300ms 지연 `invalidateSize()` 추가 호출.
- base map과 analysis area를 먼저 띄운 뒤 대형 edge layer를 비동기로 로드.
- status panel에 analysis/edge/TLS load count, center/zoom, last error 표시.

## Leaflet CSS Fallback 기록

OSM tile이 조각난 블록처럼 보이는 문제를 줄이기 위해 Leaflet critical CSS fallback을 HTML 안에 inline으로 추가했다.

추가한 fallback:

- `.leaflet-container { overflow: hidden; }`
- `.leaflet-pane`, `.leaflet-tile`, `.leaflet-tile-container`, `.leaflet-layer` 등 Leaflet 내부 layer 요소의 `position: absolute; left: 0; top: 0;`
- `.leaflet-container img { max-width: none !important; max-height: none !important; }`
- `.leaflet-tile { width: 256px; height: 256px; }`

Status panel에는 `leaflet tile position` 진단값을 표시한다. 정상값은 `absolute`다. `absolute`가 아니면 Leaflet CSS가 적용되지 않은 상태로 보고 브라우저 캐시/CDN/네트워크 상태를 확인한다.

## Layer Visibility Debug 기록

A안으로 구현한다.

- 새 Step 4.5는 만들지 않는다.
- `selectable_edges.geojson`은 만들지 않는다.
- 기존 `data_prepared/geojson/sumo_edges.geojson` 전체 21,599개를 그대로 사용한다.
- 분석권역과 SUMO edge는 기본 ON, 신호등은 기본 OFF다.
- 체크박스 상태와 `map.hasLayer(...)` 상태를 동기화한다.
- 오른쪽 상태 패널에 다음 값을 표시한다:
  - 분석권역 표시 여부
  - SUMO edge 표시 여부
  - 신호등 표시 여부
  - analysis bounds
  - edge bounds
  - TLS bounds
  - 첫 번째 edge 좌표 샘플
- 디버그 버튼:
  - `분석권역으로 이동`
  - `edge 영역으로 이동`
  - `신호등 영역으로 이동`
- edge는 디버그 확인을 위해 굵고 눈에 띄는 색상으로 표시한다.
- edge 클릭 시 오른쪽 패널에 edge 속성을 표시하고, 현재 선택 모드 목록에 추가한다.
- 선택 edge는 모드별 색상으로 강조한다.

## 시각화 미표시 원인 진단 기록

레이어가 로드 count는 뜨지만 지도 위에 보이지 않는 문제를 분리하기 위해 작은 단위의 강제 디버그 레이어를 추가한다.

- 첫 번째 edge 첫 좌표에 큰 노란색 `circleMarker`를 표시한다.
- 첫 10개 edge를 굵은 분홍색 선으로 별도 표시한다.
- analysis area를 굵은 디버그 스타일로 한 번 더 표시한다.
- TLS 10개를 큰 빨강/주황 원으로 별도 표시한다.
- 브라우저 console에 Step 4 진단 정보를 출력한다.

상태 패널과 console에서 확인할 값:

- analysis feature count
- edge feature count
- TLS feature count
- first edge id
- first edge coordinate sample
- first edge marker lat/lon
- analysis bounds
- first 10 edge bounds
- edge bounds
- TLS bounds
- `map.hasLayer(analysisLayer)`
- `map.hasLayer(edgeLayer)`
- `map.hasLayer(tlsLayer)`
- `map.hasLayer(debugEdgeLayer)`
- current center/zoom
- last error

추가 진단 버튼:

- `첫 edge로 이동`
- `first 10 edges로 이동`
- `분석권역으로 이동`
- `edge 영역으로 이동`
- `신호등 영역으로 이동`

판단 기준:

- 첫 edge marker도 안 보이면 좌표 순서, marker 생성, map pane, fit 위치 문제를 의심한다.
- first 10 debug edges가 보이면 전체 edge layer style/add/성능 문제로 좁힌다.
- debug edges는 보이는데 전체 edges가 안 보이면 전체 edge 렌더링 지연 또는 style 문제를 의심한다.
- bounds가 서울 밖이면 좌표 순서 또는 Step 3 좌표 변환 문제를 의심한다.

## 하지 않는 일

- 사고 시나리오 생성
- `selected_edges.json` 자동 확정
- 소방차 route 생성
- 일반차량 수요 생성
- TraCI controller 구현
- 신호제어 구현
- SUMO 시뮬레이션 실행
- batch simulation
- Bayesian Optimization
