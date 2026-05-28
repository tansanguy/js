# Step 8 Preflight And B0 Emergency-Only Smoke

## 목표

Step 8은 Step 7 route artifact가 active reduced net에서 실제 SUMO 실행 가능한지 확인한다.

실행 범위는 B0 emergency-only smoke 20개이다. 일반 차량 수요, 신호제어, B1/B2, full batch는 포함하지 않는다.

## Preflight 기준

- `emergency_routes.rou.xml` root가 `<routes>`여야 한다.
- route edge가 모두 `data_prepared/net/jungbu_ellipse_passenger.net.xml`에 존재해야 한다.
- route edge가 emergency 차량 class를 허용해야 한다.
- route artifact 20개가 생성되어야 한다.
- route review HTML이 생성되어야 한다.
- legacy full map은 기본 입력으로 사용하지 않는다.

Preflight 산출물:

- `data_prepared/preflight/preflight_summary.json`
- `data_prepared/preflight/preflight_report.csv`

## B0 emergency-only smoke 실행

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/06_preflight/step08_b0_emergency_only_smoke.py
```

각 사고 후보 route별로 독립 실행 디렉터리를 만든다.

- 위치: `runs/b0_emergency_only_smoke/ACC_*/`
- 차량: emergency vehicle 1대
- 일반 차량 수요: 없음
- 신호제어: 없음
- B1/B2 우선신호: 없음

## Smoke 결과

- `results/metrics/b0_emergency_only_smoke_summary.csv`
- `results/metrics/b0_emergency_only_smoke_summary.json`
- `outputs/logs/step08_b0_emergency_only_smoke.log`

각 smoke는 `exit_code`, `arrived`, `travel_time`, `route_id`, `scenario_id`, `failure_reason`을 기록한다.

## 하지 않는 일

- TOPIS demand 생성
- routeSampler 구현
- TraCI 신호제어 구현
- B1/B2 신호조작 구현
- SUMO full simulation batch
- Bayesian Optimization
