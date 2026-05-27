# Step 3 SUMO Net GeoJSON Export

## 목표

Step 3은 SUMO `net.xml`을 브라우저 지도에서 읽을 수 있는 GeoJSON 레이어로 변환하는 단계다.

HTML 지도는 만들지 않는다. Step 4의 `map_review.html`이 이 단계에서 만든 edge/TLS GeoJSON을 입력으로 사용한다.

## 입력

- `data_prepared/net/jungbu_area.net.xml`
- `data_prepared/geojson/analysis_area.geojson`
- `data_prepared/geojson/analysis_area_meta.json`
- `data_prepared/net/net_audit.json`
- `config/map_config.yaml`

## 출력

- `data_prepared/geojson/sumo_edges.geojson`
- `data_prepared/geojson/sumo_tls.geojson`
- `data_prepared/geojson/step03_geojson_audit.json`
- `outputs/logs/step03_export_geojson.log`

## 구현 원칙

- `sumolib.net.readNet`으로 `net.xml`을 읽는다.
- SUMO XY 좌표는 `net.convertXY2LonLat(x, y)`로 `[lon, lat]` 변환한다.
- edge는 `LineString`, TLS는 `Point`로 분리 저장한다.
- internal edge는 삭제하지 않고 `is_internal=true`로 표시한다.
- 사고 후보 edge 선택은 하지 않는다.
- HTML 지도, OSM 다운로드, netconvert 재실행, route/demand/controller 구현은 하지 않는다.

## Edge Properties

- `edge_id`
- `from_node`
- `to_node`
- `length_m`
- `speed_mps`
- `lane_count`
- `priority`
- `is_internal`
- `edge_function`
- `allows_passenger`
- `allows_emergency_candidate`
- `shape_point_count`

## TLS Properties

- `tls_id`
- `node_id`
- `junction_id`
- `lon`
- `lat`
- `controlled_link_count`
- `program_count`
- `phase_count`
- `inside_analysis_bbox`

TLS는 `tlLogic`, `connection tl=...`, `traffic_light` junction ID의 union으로 export한다. 이 방식은 Step 2 audit의 traffic light 기준과 맞추기 위한 것이다.

## 실행 명령

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/01_map/step03_export_geojson.py
```

## 검증 명령

```bash
cd /Users/junlee/Desktop/js
python3 -m json.tool data_prepared/geojson/sumo_edges.geojson >/dev/null
python3 -m json.tool data_prepared/geojson/sumo_tls.geojson >/dev/null
python3 -m json.tool data_prepared/geojson/step03_geojson_audit.json >/dev/null
cat outputs/logs/step03_export_geojson.log
```

## 하지 않는 일

- OSM 다운로드
- netconvert 재실행
- HTML 지도 생성
- 사고 edge 선택
- 소방차 route 생성
- 일반차량 수요 생성
- TraCI controller 구현
- 신호제어 구현
- batch simulation
- Bayesian Optimization
