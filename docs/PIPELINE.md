# B00/B0/B2 실험 파이프라인

이 문서는 최종 실행 기준만 설명한다. 과거 Step 문서는 준비와 진단 기록이며, 최종 실험 명령은 `02_simulation/run_b0_b1_b2_experiment.py`만 사용한다.

## 1. 공통 입력

- manifest: `configs/final_experiment_manifest.json`
- net: `data_prepared/net/jungbu_ellipse_passenger_speed50.net.xml`
- background demand: `data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml`
- route set: `results/metrics/b0_baseline_19route_smoke_summary.csv`에서 검증된 `b0_valid_18`
- 제외 route: `ER_ACC_013`
- B2 parameter CSV: `configs/b2_parameter_sets.csv`

`configs/b2_parameter_sets.csv` 필수 컬럼:

```csv
parameter_id,D_det,alpha,G_ext
```

고정 알고리즘 파라미터:

- `T_change_sec=30.00`: `D_det` 안에서 red/yellow/clearance이면 30초 뒤 응급차 방향 green으로 전환한다.
- `w1=3.00`, `w2=1.00`, `w3=1.00`: `score_sec = w1*A_delay_sec + w2*N_delay_sec + w3*T_recovery_sec`.
- net은 `speed50` 파생 파일을 사용하고, 응급차는 `speedFactor=1.00`, `has.bluelight.device=false`로 둔다.

실행 전 venv 준비:

```bash
bash 00_setup/setup_venv.sh
source .venv/bin/activate
bash 00_setup/verify_env.sh
```

## 2. 실험 모드

- `B00`: 배경 차량 없이 응급차 1대만 주행한다. 자유류 응급차 통행시간 기준값을 만든다.
- `B0`: 첨두시간 배경 수요와 응급차 1대를 함께 실행한다. 신호 조작은 없다.
- `B2`: B0과 같은 배경 수요와 응급차를 사용하고 corridor priority 신호 제어를 적용한다.

## 2.1 B2 안전 규칙

B2는 응급차 통행을 우선하지만, 아래 규칙을 위반하지 않는다.

- `D_det` 안에서 이미 green이면 green을 연장한다.
- `D_det` 안에서 green이 아니면 `T_change_sec=30.00` 뒤 응급차 방향 green으로 전환한다.
- 노란불과 교차로 clearance phase를 생략하지 않는다.
- 직접 red→green 순간 점프는 금지하고, 전환 전 yellow/clearance 정리시간을 둔다.
- `alpha`, `G_ext`는 정수 초로만 적용한다. `5.00`은 허용하지만 `5.5`는 실패 처리한다.
- 현재 단계의 목표는 B2 성능 최적화가 아니라 emergency stop, lane connection warning, teleport 없이 무결한 시뮬레이션을 만드는 것이다.

현재 `configs/b2_parameter_sets.csv`에는 `D_det=300,500,700,900`, `alpha=5`, `G_ext=60` 후보를 둔다. Bayesian Optimization은 추후 구현 범위다.

## 3. 파이프라인 1: `parameter_input_sim`

목적은 추후 외부 Bayesian Optimization이 사용할 입력 지표를 만드는 것이다. 현재 저장소는 최적화를 직접 수행하지 않고, CSV에 있는 B2 파라미터 조합만 실행한다.

- route: 소방서 edge `-381802881#2`에서 서울역 후보 edge `438360331#2`까지 synthetic route.
- modes: `B00`, `B0`, `B2`.
- output: `results/metrics/parameter_input_sim.csv`.
- raw run dir: `runs/final/parameter_input_sim/{run_id}/...`.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B0 B2 \
  --repeats 1 \
  --workers 1 \
  --timeout-steps 7200
```

## 4. 파이프라인 2: `final_effect_validation_sim`

목적은 최종 파라미터가 여러 목적지 route에서 효과가 있는지 검증하는 것이다.

- route set: `b0_valid_18`.
- excluded route: `ER_ACC_013`.
- modes: `B00`, `B0`, `B2`.
- output: `results/metrics/final_effect_validation_sim.csv`.
- raw run dir: `runs/final/final_effect_validation_sim/{run_id}/...`.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline final_effect_validation_sim \
  --modes B00 B0 B2 \
  --repeats 5 \
  --workers 4
```

## 5. CSV 지표 정의

핵심 컬럼:

- `pipeline,mode,parameter_id,repeat_id,route_id`
- `route_start_edge,route_target_edge,route_policy`
- `D_det,alpha,G_ext,T_change_sec,w1,w2,w3`
- `effective_alpha_sec,effective_G_ext_sec`
- `emergency_travel_time_sec,b00_emergency_travel_time_sec`
- `A_delay_sec,N_delay_sec,T_recovery_sec,score_sec`
- `emergency_arrived,emergency_teleport,background_vehicle_count,final_status,warning_reason,failure_reason,run_dir`
- `safety_violation_count,emergency_stop_warning_count,emergency_lane_connection_warning_count,signal_events_csv`
- `timeout_reached,remaining_vehicle_count,background_remaining_count,all_vehicles_arrived`
- `N_delay_completed_vehicle_edge_count,N_delay_excluded_active_vehicle_edge_count,N_delay_excluded_ratio`
- `network_avg_speed_kmh,congestion_valid,congestion_reason`
- `emergency_speed_factor,emergency_speed_cap_kmh,emergency_bluelight_enabled`
- `T_recovery_tls_count,T_recovery_max_tls_id,T_recovery_unrecovered_count`
- `queue_recovery_csv`
- `run_id,generated_at,timeout_steps,command_time_to_teleport`

지표 정의:

- `A_delay_sec`: `emergency_travel_time_sec - b00_emergency_travel_time_sec`.
- `N_delay_sec`: 전체 네트워크 일반차량 중 main/corridor edge와 internal edge를 제외한 비메인 도로에서, 완료된 차량-edge별 `(실제 체류시간 - 자유류 통과시간)` 평균. 관측 종료 시점에 아직 edge를 빠져나가지 못한 기록은 제외하고 제외량을 별도 컬럼에 저장한다.
- `T_recovery_sec`: B0/B2에서 emergency route의 모든 TLS 교차로를 대상으로, TLS별 접근 edge 대기열 합계가 emergency 통과 후 출발 전 기준 이하로 회복되는 시간의 최댓값.
- `score_sec`: `3*A_delay_sec + 1*N_delay_sec + 1*T_recovery_sec`.

`B00`의 `A_delay_sec`, `N_delay_sec`, `T_recovery_sec`는 `0.00`이다.

## 6. 판정 기준

- emergency teleport는 `FAIL`.
- background/general teleport는 `WARNING`.
- route error는 `FAIL`.
- B2 안전 규칙 위반, emergency stop warning, emergency lane connection warning은 `FAIL`.
- `timeout_steps=7200`은 2시간 관측창이다. 일반차가 남아 있어도 실패가 아니며 `PASS_WITH_REMAINING_BACKGROUND`로 남긴다.
- emergency가 도착하지 못하면 `FAIL`.
- `network_avg_speed_kmh`가 10~25km/h이면 정체 상황으로 인정한다.
- 모든 `_sec` 값은 초(s), 소수 둘째자리 문자열로 저장한다.
