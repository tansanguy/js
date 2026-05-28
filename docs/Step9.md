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
