# Step 9 TOPIS AM Background Demand

## 목표

TOPIS 검지기 AM peak count를 active reduced SUMO map의 screenline 제약으로 변환하고, routeSampler 기반 background vehicle route를 생성한 뒤 SUMO smoke로 일반 차량 출발/도착을 확인한다.

## 입력

- active net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- canonical TOPIS CSV: `data_prepared/demand/peak_volume_summary.csv`
- period: `am`
- smoke seconds: `600`

root의 `peak_volume_summary.csv`는 canonical input으로 copy하며, Step 9 실행 중에는 `data_prepared/demand/peak_volume_summary.csv`만 읽는다.

## 매핑 정책

- 검지점 좌표를 nearest edge 하나로만 확정하지 않는다.
- 지점명에서 도로명을 추출해 `road_axis_id`를 만들고, 좌표 주변 passenger 가능 edge 후보 중 대표 screenline edge 또는 양방향 edge pair를 선택한다.
- 방향별 실측값이 없으므로 양방향 edge pair는 50:50으로 분배한다.
- A-17은 0값 측정 누락으로 routeSampler count 입력에서 제외한다.
- A-19는 비정상 저값으로 routeSampler count 입력에서 제외한다.
- AM 3시간 count는 `AM_peak_avg * smoke_seconds / 10800`으로 환산한다.

## 산출물

- `data_prepared/demand/detector_to_screenline_mapping.csv`
- `data_prepared/demand/topis_screenline_counts_am.csv`
- `data_prepared/demand/topis_edgedata_am.xml`
- `data_prepared/demand/background_routes_candidate_am.rou.xml`
- `data_prepared/demand/background_routes_am.rou.xml`
- `data_prepared/demand/background_demand_summary.json`
- `results/metrics/background_vehicle_spawn_smoke_summary.csv`
- `results/metrics/background_vehicle_spawn_smoke_summary.json`
- `outputs/logs/step09_topis_background_demand.log`

## 현재 결과

- final status: `WARNING`
- TOPIS row count: `13`
- valid detector count: `11`
- excluded detector count: `2`
- expected 600s count: `3751.294444`
- routeSampler vehicle count: `3565`
- smoke departed count: `3565`
- smoke arrived count: `3565`
- smoke arrival rate: `1.000000`
- smoke teleports: `2127`
- smoke route errors: `0`

## 하지 않는 일

- emergency route 재생성
- spine route 재계산
- TraCI 신호제어
- B1/B2 구현
- Bayesian Optimization
- full batch 실행
- netconvert 실행
- OSM 다운로드
- map 재생성
- legacy full map 기본 입력 사용

## Background Demand Audit


Step 9 audit는 기존 `background_routes_am.rou.xml`을 재생성하지 않고, route XML과 audit smoke edgeData output을 기준으로 planned/actual edge usage를 분리해 검증한다.

- audit status: `WARNING`
- route XML vehicle count: `3565`
- smoke departed/arrived: `3565` / `3565`
- active passenger edge count: `3021`
- planned used edge count: `2390`
- planned coverage ratio: `0.791129`
- screenline count rows: `22`
- screenline planned low-achievement count: `0`
- screenline actual low-achievement count: `5`
- spine edge count: `258`
- spine planned coverage ratio: `0.965116`
- teleport ratio: `0.596634`
- teleport severity: `SCALE_DOWN_RECOMMENDED`

Audit outputs:

- `results/metrics/background_route_edge_counts_am.csv`
- `results/metrics/background_edge_coverage_summary.json`
- `results/metrics/background_zero_traffic_edges_am.csv`
- `results/metrics/background_screenline_count_audit_am.csv`
- `results/metrics/background_spine_edge_coverage_am.csv`
- `results/metrics/background_teleport_interpretation.json`
- `results/metrics/background_actual_edgedata_am.xml`
- `results/metrics/background_actual_edge_counts_am.csv`
- `outputs/logs/step09_background_demand_audit.log`

해석:

- 모든 reduced-map edge에 차량이 있어야 하는 것은 아니다. 전체 edge coverage는 참고 지표다.
- 핵심은 TOPIS screenline target 달성, spine/corridor coverage, detector road-axis flow 존재 여부다.
- 현재 teleport가 많으면 route 생성 실패가 아니라 capacity/lane-choice instability로 해석하고, B0/B1/B2 비교 전 0.5x 또는 0.3x scale-down audit 후보를 검토한다.

## A-17/A-19 Imputed Variant

기존 Step 9 base demand는 보존하고, A-17/A-19에 연구용 imputed screenline target을 추가한 별도 variant를 생성했다.

- variant: `am_imputed_a17_a19`
- route file: `data_prepared/demand/background_routes_am_imputed_a17_a19.rou.xml`
- screenline rows: `26`
- imputed screenline rows: `4`
- expected 600s count: `4604.377778`
- routeSampler vehicle count: `4359`
- smoke departed/arrived: `4359` / `4359`
- smoke teleports: `3024`
- teleport ratio: `0.693737`
- A-17/A-19 screenline positive: `True`
- actual coverage ratio: `0.777888`

Imputation policy:

- A-17은 같은 세종대로 valid detector `A-13`의 AM 3h count를 사용한다.
- A-19는 500m 내 valid detectors `A-13`, `A-16`, `A-12`, `A-23`의 median AM 3h count를 사용한다.
- actual screenline 달성은 `max(actual_entered_count, actual_left_count)`로 함께 판단한다.

