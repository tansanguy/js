# Step 7 Emergency Route Artifacts

## 목표

Step 7은 Step 6에서 reachable로 검증된 사고 후보 20개 전체에 대해 응급차 route artifact를 고정한다.

대표 route 1개를 고르지 않는다. 각 사고 후보마다 shortest route와 major-road-biased route를 계산하고 비교한다. 최종 route artifact는 대로 우선 route를 사용하되, shortest 대비 길이 증가율이 큰 route는 review warning으로 남긴다.

## 입력

- active net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- active edge GeoJSON: `data_prepared/geojson/ellipse_passenger_edges.geojson`
- Step 6 route check: `data_prepared/routes/route_connectivity_check.csv`
- Step 6 summary: `data_prepared/routes/route_connectivity_summary.json`

`archive/full_map_legacy/`는 reference 전용이며 Step 7 기본 입력으로 사용하지 않는다.

## 대로 우선 정책

`major-road-biased` route는 거리 비용을 기본으로 하되 다음 요소로 비용을 낮춘다.

- lane_count가 큰 edge
- speed_mps가 높은 edge
- priority가 높은 edge
- 길고 연속적인 edge
- 중부소방서-서울역 분석축과 방향이 잘 맞는 edge

길이 증가율 기준:

- major route가 shortest보다 35% 초과 길면 `WARNING`
- major route가 shortest보다 60% 초과 길면 `needs_manual_review`

## 실행

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/04_routes/step07_generate_emergency_routes.py
```

## 산출물

- `data_prepared/routes/emergency_routes.csv`
- `data_prepared/routes/emergency_routes.rou.xml`
- `data_prepared/routes/route_compare_shortest_vs_major.csv`
- `data_prepared/scenarios/accident_scenarios.csv`
- `data_prepared/routes/emergency_route_summary.json`
- `results/html/route_review.html`
- `data_prepared/manual/route_review_decisions.schema.json`
- `data_prepared/preflight/preflight_summary.json`
- `data_prepared/preflight/preflight_report.csv`
- `outputs/logs/step07_emergency_routes.log`

## Route review

`results/html/route_review.html`은 20개 route를 지도에 표시한다.

- 파란색: major-road-biased route
- 회색: shortest route
- route별 accept/reject 선택 가능
- reject reason 입력 가능
- `route_review_decisions.json` 다운로드 가능

사용자가 accept한 route만 이후 B0/B1/B2에서 같은 고정 route로 공유한다.

## 하지 않는 일

- netconvert 실행
- OSM 다운로드
- map 재생성
- 일반 차량 수요 생성
- routeSampler 구현
- 신호제어 구현
- B1/B2 신호조작 구현
- SUMO full batch 실행
- Bayesian Optimization
