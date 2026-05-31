# B00/B0/B2 실험 파이프라인

이 문서는 최종 실행 기준만 설명한다. 최종 실험 명령은 `02_simulation/run_b0_b1_b2_experiment.py`만 사용한다.

## 1. 공통 입력

- manifest: `configs/final_experiment_manifest.json`
- net: `data_prepared/net/jungbu_ellipse_passenger_speed50.net.xml`
- background demand: `data_prepared/demand/background_routes_am_imputed_a17_a19_warm0p15_sustain0p05_seed002_sustained_3600.rou.xml`
- Seoul Station straight fixed route: `data_prepared/manual/seoul_station_manual_route.json`
- B2 parameter CSV: `configs/b2_parameter_sets.csv`

`configs/b2_parameter_sets.csv` 필수 컬럼:

```csv
parameter_id,D_det,alpha,G_ext,T_change_sec
```

고정/입력 알고리즘 파라미터:

- `T_change_sec`: `D_det` 안에서 green이 아니면 후보 시간 뒤 응급차 방향 green 전환을 요청한다. 단 yellow/clearance는 생략하지 않으며, 대기 중 기존 phase sequence가 응급차 green에 도달하면 강제 전환하지 않고 그 green을 연장한다.
- `w1=3.00`, `w2=1.00`, `w3=1.00`: `score_sec = w1*A_delay_sec + w2*N_delay_sec + w3*T_recovery_sec`.
- net은 `speed50` 파생 파일을 사용하고, 응급차는 `speedFactor=1.40`, `maxSpeed=70km/h`, `has.bluelight.device=false`로 둔다. 단 `B00`은 자유류 기준이므로 SUMO `tls.all-off=true`로 신호등을 비활성화한다.
- 배경 수요는 600초 TOPIS 패턴을 3600초까지 반복하되, 0~600초 warm-up은 0.15x, 이후 지속 수요는 0.05x(`sustained_calibration_seed_002`)를 사용한다. 기본 응급차 출동 시점은 600초다.

실행 전 venv 준비:

```bash
bash 00_setup/setup_venv.sh
source .venv/bin/activate
bash 00_setup/verify_env.sh
```

## 2. 실험 모드

- `B00`: 배경 차량 없이 신호등을 비활성화하고 응급차 1대만 주행한다. 신호 대기 없는 자유류 응급차 통행시간 기준값을 만든다.
- `B0`: 지속 첨두시간 배경 수요와 응급차 1대를 함께 실행한다. 신호 조작은 없다.
- `B2`: B0과 같은 지속 배경 수요와 응급차를 사용하고 corridor priority 신호 제어를 적용한다.

## 2.1 B2 안전 규칙

B2는 강제 preemption이 아니라 안전한 priority 제어다. 응급차 통행을 우선하지만, 아래 규칙을 위반하지 않는다.

- `D_det` 안에서 이미 green이면 green을 연장한다.
- `D_det` 안에서 green이 아니면 CSV의 `T_change_sec` 뒤 응급차 방향 green 전환을 요청한다.
- 노란불과 교차로 clearance phase를 생략하지 않는다.
- 직접 red→green 순간 점프는 금지하고, 전환 전 yellow/clearance 정리시간을 둔다.
- 대기 중 기존 phase sequence가 응급차 green에 도달하면 `switch_to_green_after_t_change` 대신 `extend_green`으로 기록한다.
- `green_arrived_before_t_change_extension_count`는 이 경우를 별도 집계한다.
- `alpha`, `G_ext`, `T_change_sec`는 정수 초로만 적용한다. `5.00`은 허용하지만 `5.5`는 실패 처리한다.
- 현재 단계의 목표는 B2 성능 최적화가 아니라 emergency stop, lane connection warning, teleport 없이 무결한 시뮬레이션을 만드는 것이다.

현재 기본 `configs/b2_parameter_sets.csv`에는 선택 후보 `D_det=1000, alpha=5, G_ext=60, T_change_sec=10`을 둔다. Bayesian Optimization 추천 모드는 기존 B2 결과 CSV를 초기 관측값으로 사용한다.

## 3. 파이프라인: `parameter_input_sim`

목적은 Bayesian Optimization이 사용할 입력 지표를 만들고, 추천된 B2 파라미터 조합을 같은 러너로 실행하는 것이다.

- route: **서울역 직선 고정 경로**. `FIRE_TO_SEOUL_STATION`, `straight_seoul_station_fixed`, 소방서 edge `-381802881#2`에서 서울역 edge `619147738#0`까지의 59-edge route다.
- modes: `B00`, `B0`, `B2`.
- output: `results/metrics/parameter_input_sim/{run_id}/experiment_results.csv`.
- score output: `results/metrics/parameter_input_sim/{run_id}/result_score.csv`.
- latest pointer: `results/metrics/parameter_input_sim/latest.json`.
- raw run dir: `runs/final/parameter_input_sim/{run_id}/...`.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B0 B2 \
  --repeats 1 \
  --workers 1 \
  --emergency-depart 600 \
  --timeout-steps 7200 \
  --recovery-buffer-sec 300
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
- `N_delay_completed_vehicle_edge_count,N_delay_censored_vehicle_edge_count,N_delay_censored_ratio`
- `N_delay_excluded_active_vehicle_edge_count,N_delay_excluded_ratio`
- `network_avg_speed_kmh,network_avg_speed_at_analysis_end_kmh,network_running_at_analysis_end`
- `network_speed_pre_emergency_kmh,network_speed_during_response_kmh,network_speed_post_recovery_kmh`
- `active_vehicle_count_pre_emergency,active_vehicle_count_during_response,active_vehicle_count_post_recovery`
- `rolling_congestion_valid,rolling_congestion_reason,rolling_congestion_min_kmh,rolling_congestion_max_kmh`
- `congestion_valid,congestion_valid_at_analysis_end,congestion_reason_at_analysis_end`
- `analysis_end_time_sec,analysis_stop_reason,recovery_buffer_sec`
- `emergency_route_length_m,emergency_route_length_source`
- `emergency_speed_factor,emergency_speed_cap_kmh,emergency_bluelight_enabled`
- `green_extension_count,green_arrived_before_t_change_extension_count,phase_switch_count`
- `T_recovery_tls_count,T_recovery_max_tls_id,T_recovery_unrecovered_count`
- `queue_recovery_csv`
- `run_id,generated_at,timeout_steps,command_time_to_teleport`

지표 정의:

- `A_delay_sec`: `emergency_travel_time_sec - b00_emergency_travel_time_sec`.
- `N_delay_sec`: 응급차 출동 시점부터 `analysis_end_time_sec`까지, 전체 네트워크 일반차량 중 main/corridor edge와 internal edge를 제외한 비메인 도로에서 차량-edge별 `(실제 체류시간 - 자유류 통과시간)` 평균. 출동 전부터 edge에 있던 차량은 출동 이후 겹친 체류분만 반영한다. `analysis_end_time_sec`에 아직 edge를 빠져나가지 못한 기록은 종료 시점까지의 부분 체류시간으로 포함하고 `N_delay_censored_*` 컬럼에 별도 표시한다.
- `T_recovery_sec`: B0/B2에서 emergency route의 모든 TLS 교차로를 대상으로, TLS별 접근 edge 대기열 합계가 emergency 통과 후 출발 전 기준 이하로 회복되는 시간의 최댓값.
- `score_sec`: `3*A_delay_sec + 1*N_delay_sec + 1*T_recovery_sec`.
- `emergency_route_length_m`: 공식 보고용 route 길이. 서울역 직선 고정 경로는 외부 edge 합산 `2990.17m`를 사용한다.
- `rolling_congestion_valid`: 300초 rolling 평균 네트워크 속도가 관측창 동안 12~35km/h 안에 있으면 `True`다. 마지막 순간 속도는 보조 진단으로만 본다.

`timeout_steps=7200`은 최대 관측창이다. B0/B2의 primary metric 관측은 emergency가 도착하고, route TLS 대기열이 회복되고, `recovery_buffer_sec`가 지난 시점에서 종료한다. 이 종료 시점은 `analysis_end_time_sec`에 저장한다.

`B00`의 `A_delay_sec`, `N_delay_sec`, `T_recovery_sec`는 `0.00`이다.

`result_score.csv`는 시행별 scoring 입력만 담는 경량 파일이다. 컬럼은 `run_id,pipeline,mode,parameter_id,repeat_id,route_id,A_delay_sec,N_delay_sec,T_recovery_sec`만 둔다.

## 6. Bayesian Optimization 추천 모드

BO 추천 모드는 SUMO를 실행하지 않는다. 기존 결과 CSV의 `mode=B2` row를 초기 관측값으로 사용해 추가 θ를 추천하고, 사용자가 실행할 명령어를 출력한다.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bayesian true \
  --bo-initial-results results/metrics/parameter_input_sim/{run_id}/experiment_results.csv \
  --bo-recommend-count 15
```

입력 CSV는 여러 개를 받을 수 있다. 필수 컬럼은 `D_det`, `alpha`, `G_ext`, `A_delay_sec`, `N_delay_sec`, `T_recovery_sec`, `score_sec`이며, `Score`만 있으면 `score_sec`로 읽는다. 실패, emergency 미도착, emergency teleport, route error, SUMO 오류 row는 GP 학습에서 제외하고 `bo_excluded_observations.csv`에 사유를 남긴다.

BO는 `D_det`, `alpha`, `G_ext`만 최적화하고 `T_change_sec=10`으로 고정한다. 탐색 범위는 유효 기존 관측치의 min/max이며, 후보 격자는 `D_det` 50m, `alpha` 1초, `G_ext` 5초 단위다. acquisition은 minimization용 Expected Improvement이고 `xi=0.05`로 exploration을 유지한다.

출력:

- `results/metrics/parameter_input_sim_bo/{bo_run_id}/bo_observations.csv`
- `results/metrics/parameter_input_sim_bo/{bo_run_id}/bo_excluded_observations.csv`
- `results/metrics/parameter_input_sim_bo/{bo_run_id}/bo_recommendations.csv`
- `results/metrics/parameter_input_sim_bo/{bo_run_id}/bo_commands.sh`
- `results/metrics/parameter_input_sim_bo/{bo_run_id}/bo_summary.json`
- `configs/generated/b2_bo_recommendations_{bo_run_id}.csv`
- `configs/generated/b2_bo_top3_reeval_{bo_run_id}.csv`

## 7. 판정 기준

- emergency teleport는 `FAIL`.
- background/general teleport는 `WARNING`.
- route error는 `FAIL`.
- B2 안전 규칙 위반, emergency stop warning, emergency lane connection warning은 `FAIL`.
- `timeout_steps=7200`은 최대 2시간 관측창이다. 일반차가 남아 있어도 실패가 아니며 `PASS_WITH_REMAINING_BACKGROUND`로 남긴다.
- emergency가 도착하지 못하면 `FAIL`.
- `rolling_congestion_valid=True`이면 실험 관측창 동안 정체가 유지된 것으로 인정한다.
- `network_avg_speed_at_analysis_end_kmh`와 `congestion_valid_at_analysis_end`는 종료 순간 잔류 상태를 보는 보조 진단이다.
- 모든 `_sec` 값은 초(s), 소수 둘째자리 문자열로 저장한다.
