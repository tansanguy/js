# Step 1 분석권역 정의

## 목표

Step 1은 중부소방서와 서울역 좌표를 기준으로 이후 Step 2~4에서 공통으로 사용할 공간 기준 파일을 만드는 단계다.

이 단계는 맵 생성 단계가 아니다. OSM 다운로드, SUMO `net.xml` 생성, SUMO edge GeoJSON 변환, HTML 지도 생성, TraCI controller, 신호제어, route 생성, demand 생성, batch simulation, Bayesian Optimization은 구현하지 않았다.

## 연구 해석

- 중부소방서는 긴급차량 출발 기준점이다.
- 서울역은 사고 도착점이 아니라 분석권역 기준점이다.
- 실제 사고 도착지는 이후 SUMO에서 도달 가능한 edge 중 고정 시나리오로 선정한다.
- 연구 핵심은 중부소방서-서울역을 잇는 대로축에서 응급차량이 빨리 빠질 수 있는 신호체계를 만드는 것이다.
- 따라서 타원 장축은 중부소방서 좌표와 서울역 좌표를 잇는 선분으로 고정했다.

## 완료 내용

- `config/map_config.yaml`에 고정 좌표와 분석권역 설정을 반영했다.
- `common/geo_utils.py`에 지리 계산 helper를 추가했다.
- `01_prepare/01_map/step01_define_area.py`를 추가했다.
- `analysis_area_meta.json`과 `analysis_area.geojson` 산출물을 생성했다.
- `outputs/logs/step01_define_area.log` 실행 로그를 생성했다.
- Step 0 환경 검증에 Python `yaml` import 체크를 추가했다.
- PyYAML이 없는 상태를 확인했고, `PyYAML-6.0.3`을 설치해 검증을 통과시켰다.

## 입력 설정

`config/map_config.yaml`의 핵심 값:

```yaml
locations:
  jungbu_fire_station:
    name: Jungbu Fire Station
    lat: 37.564875
    lon: 127.015376
  seoul_station:
    name: Seoul Station Reference
    lat: 37.558488
    lon: 126.971443

analysis_area:
  shape: ellipse
  ellipse_width_m: 1800
  bbox_buffer_m: 800
  ellipse_num_points: 128
```

## 구현 파일

- `common/geo_utils.py`
  - `validate_lat_lon`
  - `haversine_distance_m`
  - `initial_bearing_deg`
  - `midpoint_latlon`
  - `latlon_to_local_xy_m`
  - `local_xy_m_to_latlon`
  - `oriented_ellipse_polygon`
  - `bbox_from_points`
  - `expand_bbox_m`
  - `geojson_feature`

- `01_prepare/01_map/step01_define_area.py`
  - `config/map_config.yaml` 로드
  - 좌표/shape/폭/buffer/point 수 검증
  - 중부소방서-서울역 거리 계산
  - midpoint 기반 타원 중심 계산
  - bearing 계산
  - 회전 타원 polygon 생성
  - 타원 포함 bbox 계산 후 buffer 확장
  - meta JSON, GeoJSON, 로그 저장

## 산출물

- `data_prepared/geojson/analysis_area_meta.json`
- `data_prepared/geojson/analysis_area.geojson`
- `outputs/logs/step01_define_area.log`

## 계산 결과

- `major_axis_m`: `3937.020`
- `semi_major_axis_m`: `1968.510`
- `minor_axis_m`: `1800.000`
- `semi_minor_axis_m`: `900.000`
- `bearing_deg`: `259.620856`
- `bbox_wgs84.min_lon`: `126.96229053834942`
- `bbox_wgs84.min_lat`: `37.54591301473934`
- `bbox_wgs84.max_lon`: `127.02452657850698`
- `bbox_wgs84.max_lat`: `37.5774540549604`

## GeoJSON Feature 구성

`analysis_area.geojson`은 valid `FeatureCollection`이며 Feature 5개를 포함한다.

- `jungbu_fire_station` Point
- `seoul_station_reference` Point
- `analysis_axis` LineString
- `analysis_ellipse` Polygon
- `osm_extract_bbox` Polygon

## 검증 기준

- `map_config.yaml` 좌표는 숫자여야 한다.
- lat은 `-90..90`, lon은 `-180..180`이어야 한다.
- `analysis_area.shape`는 `ellipse`만 허용한다.
- `ellipse_width_m > 0`.
- `bbox_buffer_m >= 0`.
- `ellipse_num_points >= 16`.
- 중부소방서와 서울역 거리가 0이면 실패한다.
- bbox polygon은 ellipse polygon 전체를 포함해야 한다.
- 산출 GeoJSON은 valid `FeatureCollection`이어야 한다.

## 실행 명령

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/01_map/step01_define_area.py
```

## 검증 명령

```bash
cd /Users/junlee/Desktop/js
python3 00_setup/verify_env.py
python3 01_prepare/01_map/step01_define_area.py
python3 -m json.tool data_prepared/geojson/analysis_area_meta.json >/dev/null
python3 -m json.tool data_prepared/geojson/analysis_area.geojson >/dev/null
cat outputs/logs/step01_define_area.log
```

## 다음 단계 연결

- Step 2는 `bbox_wgs84`를 OSM 추출 범위로 사용한다.
- Step 3은 `analysis_area.geojson`을 지도 검토 레이어로 사용한다.
- Step 4는 사람이 HTML 지도에서 분석 edge, 사고 후보 edge, 제외 edge를 선택한다.
- 실제 SUMO edge 포함 여부와 사고 도착지 선정은 Step 1에서 하지 않는다.
