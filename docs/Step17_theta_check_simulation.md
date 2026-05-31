# Step 17 Theta Check Simulation

`05_theta_check_simulation`은 Bayesian Optimization으로 선택한 B2 theta를 `b0_valid_18` route set에서 최종 검증하는 독립 파이프라인이다.

기존 `02_simulation` 파이프라인과 `04_시각화` 작업 영역을 수정하지 않고, 입력 route snapshot과 실행 결과를 모두 05번 namespace 아래에 둔다.

## 입력

기본 route 입력은 05번 폴더 내부의 18-route snapshot이다.

- `05_theta_check_simulation/routes/b0_valid_18_routes.csv`
- `05_theta_check_simulation/routes/b0_valid_18_routes_manifest.json`

`ER_ACC_013`은 기존 B0 smoke에서 emergency teleport가 발생했기 때문에 제외했다. 원본 19-route CSV는 `data_prepared/routes/emergency_routes_spine_v2.csv`에 그대로 둔다.

기본 시뮬레이션 입력은 다음 파일을 사용한다.

- net: `data_prepared/net/jungbu_ellipse_passenger_speed50.net.xml`
- background demand: `data_prepared/demand/background_routes_am_imputed_a17_a19_warm0p15_sustain0p05_seed002_sustained_3600.rou.xml`
- TLS audit: `data_prepared/signals/tls_phase_audit_spine_v2.csv`
- priority terminals: `data_prepared/signals/priority_terminal_candidates.csv`
- B2 theta CSV: `configs/b2_parameter_sets.csv`

최종 검증에서는 기본 CSV와 별개로 05번 폴더 내부의 전용 1-row CSV를 명시적으로 사용한다.

- `05_theta_check_simulation/final_optimum_b2_parameter_sets.csv`
- `D_det=450`, `alpha=6`, `G_ext=51`, `T_change_sec=10`

## 파이프라인

### 1. Route Connectivity Smoke

18개 route를 `B00` 자유류 조건에서 1회씩 실행한다. 배경차량 없이 route 연결성, 응급차 도착, emergency teleport, route error 여부만 확인한다.

```bash
python 05_theta_check_simulation/parameter_sim.py \
  --modes B00 \
  --repeats 1 \
  --workers 6 \
  --output-prefix route_connectivity_smoke \
  --resume
```

성공 기준은 `final_status: PASS`, task 18개 전부 `PASS`, emergency teleport 없음, route error 없음이다.

### 2. Single-Route Functional Smoke

전체 batch 전에 단일 route를 `B00`, `B0`, `B2`로 실행해 SUMO/TraCI, B2 제어, metric 집계, resume output 갱신이 정상인지 확인한다.

```bash
python 05_theta_check_simulation/parameter_sim.py \
  --routes ER_ACC_001 \
  --modes B00 B0 B2 \
  --repeats 1 \
  --workers 1 \
  --output-prefix parameter_sim_smoke \
  --resume
```

`WARNING`은 background vehicle teleport처럼 응급차가 아닌 경고가 있을 때 발생할 수 있다. emergency가 도착하고 emergency teleport/route error가 없다면 기능 smoke로는 통과로 본다.

### 3. Full Theta Validation

18개 route 전체를 `B00`, `B0`, `B2`로 실행한다. 응급차 출발 시간은 route/repeat별로 `--seed`와 `550~650`초 window에서 재현 가능하게 생성한다.

```bash
python 05_theta_check_simulation/parameter_sim.py \
  --modes B00 B0 B2 \
  --routes-csv 05_theta_check_simulation/routes/b0_valid_18_routes.csv \
  --output-prefix parameter_sim \
  --b2-params configs/b2_parameter_sets.csv \
  --depart-min 550 \
  --depart-max 650 \
  --seed 20260531 \
  --repeats 1 \
  --workers 6 \
  --resume
```

## Resume 정책

실행 중 끊겨도 같은 명령으로 이어서 실행할 수 있다.

- 실행 시작 시 `task_manifest.json`을 먼저 저장한다.
- task마다 `task_status.json`을 저장한다.
- `--resume`이면 `PASS`, `WARNING`, `FAIL`로 완료된 task는 건너뛴다.
- `RUNNING`, 누락, 깨진 status 파일은 재실행 대상으로 본다.
- aggregate CSV/JSON은 task 완료마다 갱신한다.
- 완전히 새로 돌리고 싶으면 `--output-prefix`를 새 이름으로 주거나 `--run-id`를 명시한다.

## 주요 옵션

- `--modes B00 B0 B2`: 실행할 mode 선택.
- `--routes ER_ACC_001 ER_ACC_002`: 특정 route만 실행.
- `--routes-csv PATH`: route CSV 교체.
- `--exclude-routes ER_ACC_013`: route 제외 목록.
- `--b2-params PATH`: B2 theta CSV 교체.
- `--depart-min 550 --depart-max 650`: 응급차 출발 시간 범위.
- `--seed 20260531`: 출발 시간 재현 seed.
- `--repeats N`: route/mode 반복 횟수.
- `--b00-repeats N`, `--b0-repeats N`, `--b2-repeats N`: mode별 반복 횟수 override.
- `--workers N`: 병렬 worker 수.
- `--resume`: 기존 task status 기준으로 이어서 실행.
- `--output-prefix NAME`: 결과 namespace.

## 출력

실행 산출물은 05번 namespace 아래에만 저장한다.

- `runs/05_theta_check_simulation/{output_prefix}/{run_id}/`
- `results/metrics/05_theta_check_simulation/{output_prefix}/{run_id}/`
- `results/metrics/05_theta_check_simulation/{output_prefix}/latest.json`

주요 결과 파일:

- `task_manifest.json`: route, mode, repeat, parameter, depart time, run dir를 포함한 실행 계획.
- `experiment_results.csv`: task-level 결과.
- `route_summary.csv`: route-level B00/B0/B2 비교 요약.
- `score_components.csv`: metric 검토용 task-level row.
- `experiment_summary.json`: 전체 상태, route 개선 수, 실패 route, signal burden 요약.
- `signal_events.csv`: B2 task별 신호 제어 이벤트.
- `task_status.json`: task별 resume marker와 저장된 result row.

## 지표 해석

- `A_delay_sec`: 같은 route/repeat의 B00 자유류 travel time 대비 B0/B2 응급차 지연.
- `N_delay_sec`: 응급차 출동 이후 비-main road 일반차 지연 평균.
- `T_recovery_sec`: route TLS queue 회복시간의 최댓값.
- `score_sec`: `3*A_delay + N_delay + T_recovery`.
- `B2_vs_B0_travel_time_delta_sec`: B2 응급차 travel time에서 B0 travel time을 뺀 값. 음수면 B2 개선.
- `realized_extension_sec`: B2 green extension에서 post-pass trim을 뺀 실제 green burden.
- `trimmed_green_sec`: 응급차 통과 후 `alpha`로 잘라낸 green 시간.

## 상태 해석

- `PASS`: 응급차 도착, emergency teleport 없음, route error 없음, background warning 없음.
- `WARNING`: 응급차는 성공했지만 background vehicle teleport 같은 비응급차 경고가 있음.
- `FAIL`: 응급차 미출발/미도착, emergency teleport, route error, SUMO 오류, task crash.

## 최근 Smoke 결과

`route_connectivity_smoke`를 `B00` mode로 실행한 결과 18개 route가 모두 정상 연결로 확인됐다.

- run id: `20260531T073014_339522Z0000`
- task count: `18`
- final status: `PASS`
- emergency arrived: all true
- emergency teleport: none
- route error: none
- travel time range: `61.0s ~ 274.0s`

## 최종 최적해 검증 결과

최종 theta는 `05_theta_check_simulation/final_optimum_b2_parameter_sets.csv`의 `D_det=450`, `alpha=6`, `G_ext=51`, `T_change_sec=10`이다.

18-route screening은 `final_optimum_route_screening` output-prefix로 실행했고, 응급차 도착, emergency teleport 없음, route error 없음, paired B0/B2 비교 가능, B2 개선폭, 실제 signal intervention을 기준으로 다음 3개 route를 선택했다.

| rank | route_id | screening B2-B0 delta sec | intervention count |
| --- | --- | ---: | ---: |
| 1 | ER_ACC_010 | -117.00 | 10 |
| 2 | ER_ACC_008 | -102.00 | 4 |
| 3 | ER_ACC_012 | -81.00 | 7 |

최종 검증은 `final_optimum_validation` output-prefix로 실행했다.

- run id: `20260531T151900_688506Z0000`
- task count: `183` (`B00=3`, `B0=90`, `B2=90`)
- final status: `PASS`
- seed: `20260531`
- depart window: `300~2400s`
- B0/B2 paired departure: all matched
- route별 30개 departure: all unique

| route | pairs | B0 mean sec | B2 mean sec | delta mean sec | improvement pct |
| --- | ---: | ---: | ---: | ---: | ---: |
| ER_ACC_008 | 30 | 232.77 | 132.47 | -100.30 | 42.79 |
| ER_ACC_010 | 30 | 257.80 | 148.33 | -109.47 | 42.72 |
| ER_ACC_012 | 30 | 225.93 | 144.50 | -81.43 | 35.83 |
| ALL | 90 | 238.83 | 141.77 | -97.07 | 40.44 |

최종 compact report는 `results/metrics/05_theta_check_simulation/final_optimum_validation_report/final_optimum_validation.md`에 있다.
