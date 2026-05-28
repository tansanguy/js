# Step 7 Emergency Route Artifacts

## 목표

Step 7은 Step 6에서 reachable로 검증된 사고 후보 20개 전체에 대해 응급차 route artifact를 고정한다.

대표 route 1개를 고르지 않는다. 각 사고 후보마다 `shortest`, `major-road-biased`, `spine-corridor-biased` route를 비교하고, 최종 artifact는 `spine-corridor-biased` route를 사용한다.

## 입력

- active net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- active edge GeoJSON: `data_prepared/geojson/ellipse_passenger_edges.geojson`
- Step 6 route check: `data_prepared/routes/route_connectivity_check.csv`
- Step 6 summary: `data_prepared/routes/route_connectivity_summary.json`

`archive/full_map_legacy/`는 reference 전용이며 Step 7 기본 입력으로 사용하지 않는다.

## Spine corridor 정책

단순 waypoint 방식은 사용하지 않는다. waypoint는 중심도로를 한 번 찍고 바로 빠질 수 있으므로, 중심도로를 척추처럼 길게 이용한다는 목표를 보장하지 못한다.

Step 7은 먼저 `corridor_spine_edges.csv`를 만든다. spine edge 후보는 active reduced GeoJSON/net 기준으로 다음 조건과 점수를 사용한다.

- `allows_passenger=true`, `is_internal=false`
- `length_m >= 10`
- 중부소방서-서울역 분석축 주변 buffer 내부
- 높은 `lane_count`, `speed_mps`, `priority`
- 분석축 방향과 높은 정렬도

각 사고 후보에 대해 여러 spine bias 강도 route 후보를 만들고, `spine_length_ratio`, `max_consecutive_spine_length_m`, shortest 대비 증가율을 함께 scoring해 최종 spine route를 고른다.

기본 review 기준:

- shortest 대비 길이 증가율이 25% 초과면 `WARNING`
- shortest 대비 길이 증가율이 40% 초과면 `needs_manual_review`
- `spine_length_ratio < 0.25`이면 `needs_manual_review`
- `max_consecutive_spine_length_m < 300`이면 `needs_manual_review`

## 실행

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/04_routes/step07_generate_emergency_routes.py
```

## 산출물

- `data_prepared/routes/corridor_spine_edges.csv`
- `data_prepared/routes/emergency_routes.csv`
- `data_prepared/routes/emergency_routes.rou.xml`
- `data_prepared/routes/route_compare_shortest_major_spine.csv`
- `data_prepared/scenarios/accident_scenarios.csv`
- `data_prepared/routes/emergency_route_summary.json`
- `results/html/route_review.html`
- `data_prepared/manual/route_review_decisions.schema.json`
- `data_prepared/preflight/preflight_summary.json`
- `data_prepared/preflight/preflight_report.csv`
- `outputs/logs/step07_emergency_routes.log`

## Route review

`results/html/route_review.html`은 20개 route를 지도에 표시한다.

- solid color: 최종 spine-corridor-biased route
- semi-transparent color: major-road-biased route
- dashed gray: shortest route
- 얇은 dark line: corridor spine edge set
- route별 accept/reject 선택 가능
- reject reason 입력 가능
- `route_review_decisions.json` 다운로드 가능

사용자가 accept한 spine route만 이후 B0/B1/B2에서 같은 고정 route로 공유한다.

## Spine route v2

v1 route review 후 ER_ACC_020은 삭제 후보로 확정하고, 나머지 19개 사고 후보를 같은 v2 scoring으로 다시 생성한다. 기존 Step 7 산출물은 덮어쓰지 않고 v2 전용 파일만 추가한다.

실행:

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/04_routes/step07_generate_emergency_routes.py --variant spine-v2
python3 01_prepare/06_preflight/step08_b0_emergency_only_smoke.py --variant spine-v2
```

v2 개선 사항:

- `spine_length_ratio`, `spine_length_m`, `max_consecutive_spine_length_m`를 더 강하게 scoring한다.
- spine 진입 후 짧게 이탈하는 route와 shortest 대비 과도한 우회를 경고한다.
- 연속 edge heading change로 `max_turn_angle`, `sharp_turn_count`, `uturn_like_transition_count`를 기록한다.
- route geometry 기준 `segment_count`, `gap_count`, `max_gap_distance_m`, `repeated_edge_count`를 기록해 “경로가 두 개처럼 보임”을 진단한다.
- `results/html/route_review_spine_v2.html`은 v2 spine route만 기본 ON이며, old spine / major / shortest는 비교 토글로 기본 OFF이다.

v2 산출물:

- `data_prepared/manual/route_review_decisions_spine.json`
- `data_prepared/routes/deleted_route_candidates.csv`
- `data_prepared/routes/spine_route_improvement_targets.csv`
- `data_prepared/routes/route_compare_shortest_major_spine_v2.csv`
- `data_prepared/routes/emergency_routes_spine_v2.csv`
- `data_prepared/routes/emergency_routes_spine_v2.rou.xml`
- `data_prepared/routes/spine_route_turn_diagnostics.csv`
- `data_prepared/routes/emergency_route_summary_spine_v2.json`
- `results/html/route_review_spine_v2.html`
- `results/metrics/b0_emergency_only_smoke_spine_v2_summary.csv`
- `results/metrics/b0_emergency_only_smoke_spine_v2_summary.json`
- `outputs/logs/step07_spine_route_v2_plan.log`
- `outputs/logs/step08_b0_emergency_only_smoke_spine_v2.log`

현재 v2 실행 결과:

- route count: 19
- deleted route: `ER_ACC_020` / target edge `381802879#1`
- Step 7 v2 preflight: `WARNING`
- B0 emergency-only smoke v2: `PASS`, 19/19 arrived

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
