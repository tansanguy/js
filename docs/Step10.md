# Step 10 B0 Baseline Speed Smoke

## 목표

신호제어 없는 B0 조건에서 imputed A-17/A-19 background demand와 응급차 `ER_ACC_002` 1대를 함께 실행하고 평균속도와 teleport를 측정한다.

## 입력

- active net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- background route: `data_prepared/demand/background_routes_am_imputed_a17_a19.rou.xml`
- emergency route: `ER_ACC_002`
- emergency vehicle id: `emergency_ER_ACC_002`
- emergency depart: `0.0`
- time-to-teleport: `1200`
- collision action: `warn`
- SUMO end policy: `no_explicit_end_run_until_all_vehicles_finished`

## 결과

- final status: `FAIL`
- SUMO exit code: `0`
- emergency departed/arrived/teleport: `True` / `True` / `True`
- emergency travel time: `10011.0`
- background departed/arrived: `4359` / `4359`
- general vehicle teleport count/ratio: `1830` / `0.419821`
- route error count: `0`
- network mean speed: `0.931561` km/h
- spine mean speed: `0.56697` km/h
- emergency-route-corridor mean speed: `0.351154` km/h
- demand scale-down recommended: `True`

## 산출물

- `runs/b0_baseline_speed_smoke/`
- `results/metrics/b0_baseline_speed_smoke_summary.csv`
- `results/metrics/b0_baseline_speed_smoke_summary.json`
- `results/metrics/b0_baseline_edge_speed.csv`
- `results/metrics/b0_baseline_spine_speed_summary.csv`
- `outputs/logs/step10_b0_baseline_speed_smoke.log`

## Demand Scale Calibration

0.5x / 0.3x / 0.2x / 0.15x / 0.12x / 0.10x background demand를 deterministic sampling으로 생성하고 ER_ACC_002 B0 no-control speed smoke를 비교했다.

- final status: `WARNING`
- recommended scale: `0.12`
- eligible scale count: `6`
- blocker: ``
- compared scales: `[0.1, 0.12, 0.15, 0.2, 0.3, 0.5]`
- summary CSV: `results/metrics/b0_demand_scale_calibration_summary.csv`
- road-axis CSV: `results/metrics/b0_road_axis_speed_calibration.csv`
- 0.2x vs 0.3x teleport diagnostic: `0.2x ratio 0.019495 > 0.3x 0.001529; stderr diagnostics show top reasons 0.2x='waited too long (jam)', 0.3x='waited too long (yield)'. This can happen because deterministic subsets are not nested; lower scale is sampled independently from 1.0x, not from 0.3x.`

추천 기준은 emergency teleport 없음, route error 0, emergency arrived, 평균속도 목표범위 근접성이다. 1.0x imputed demand는 gridlock/stress demand로 보관한다.

## 19-Route B0 Baseline Smoke

0.15x imputed background demand를 고정 입력으로 사용해 spine-v2 emergency route 19개 전체를 B0 no-control 조건에서 route별 1회씩 실행했다.

- final status: `FAIL`
- background route: `data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml`
- route count: `19`
- PASS/WARNING/FAIL: `{'PASS': 9, 'WARNING': 9, 'FAIL': 1}`
- emergency teleport route count: `1`
- route error route count: `0`
- Step 11B allowed: `True`
- summary CSV: `results/metrics/b0_baseline_19route_smoke_summary.csv`
- summary JSON: `results/metrics/b0_baseline_19route_smoke_summary.json`
- speed CSV: `results/metrics/b0_baseline_19route_speed_by_route.csv`

