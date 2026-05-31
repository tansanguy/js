# 최종 실험 실행 안내서

## 단일 진입점

최종 실험은 아래 러너만 사용한다.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py --manifest configs/final_experiment_manifest.json ...
```

준비/진단 스크립트는 보조 도구다. 최종 CSV 산출 명령으로 사용하지 않는다.

## venv 준비

```bash
cd /Users/junlee/Desktop/js
bash 00_setup/setup_venv.sh
source .venv/bin/activate
bash 00_setup/verify_env.sh
```

`verify_env.sh`는 `.venv/bin/python`을 자동으로 우선 사용한다. SUMO 실행 파일은 별도 시스템 설치가 필요하다.

## 모드

- `B00`: 배경 차량 없이 신호등을 비활성화한 응급차 자유류 run.
- `B0`: 600초 warm-up 후에도 지속되는 첨두시간 배경 수요 + 응급차, 신호 조작 없음.
- `B2`: B0과 같은 지속 수요 + corridor priority 제어.

`B1`은 최종 비교 대상에서 제외한다.

## B2 안전 규칙

B2는 강제 preemption이 아니라 안전한 priority 제어다. 안전 규칙을 우선한다.

- `D_det` 안에서 이미 green이면 green 시간을 연장한다.
- green이 아니면 CSV의 `T_change_sec` 뒤 응급차 방향 green 전환을 요청한다.
- 노란불과 clearance phase를 건너뛰지 않는다.
- 직접 red→green 순간 점프는 금지하고, 전환 전 yellow/clearance 정리시간을 둔다.
- 대기 중 기존 phase sequence가 응급차 green에 도달하면 강제 전환하지 않고 해당 green을 연장한다.
- 보행자 최소 보행시간 보호를 위해 현재 phase를 단축하지 않는다.
- `alpha`, `G_ext`, `T_change_sec`는 정수 초만 허용한다.

이번 단계의 성공 기준은 B2가 B0보다 빠른지만이 아니라, emergency teleport, emergency stop warning, lane connection warning 없이 끝나는지다. 기본 선택값은 `D_det=1000, alpha=5, G_ext=60, T_change_sec=10`이다. Bayesian Optimization 전체 구현은 추후 범위다.

고정값:

- `w1=3.00`, `w2=1.00`, `w3=1.00`
- `score_sec = w1*A_delay_sec + w2*N_delay_sec + w3*T_recovery_sec`
- net은 `speed50` 파생 파일을 사용하고, 응급차는 `speedFactor=1.40`, `maxSpeed=70km/h`, `has.bluelight.device=false`로 둔다. 단 `B00`은 자유류 기준이므로 SUMO `tls.all-off=true`로 신호등을 비활성화한다.
- 배경 수요는 600초 TOPIS 패턴을 3600초까지 반복하되, 0~600초 warm-up은 0.15x, 이후 지속 수요는 0.05x(`sustained_calibration_seed_002`)를 사용한다. 기본 응급차 출동은 600초다.
- 파라미터 입력 경로는 **서울역 직선 고정 경로** 하나만 사용한다. `route_id=FIRE_TO_SEOUL_STATION`, `route_policy=straight_seoul_station_fixed`, `target_edge=619147738#0`, 공식 길이는 외부 edge 합산 `2990.17m`다.

## 파라미터 입력 실험

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

출력:

- `results/metrics/parameter_input_sim/{run_id}/experiment_results.csv`
- `results/metrics/parameter_input_sim/{run_id}/score_components.csv`
- `results/metrics/parameter_input_sim/{run_id}/result_score.csv`
- `results/metrics/parameter_input_sim/{run_id}/experiment_summary.json`
- `results/metrics/parameter_input_sim/latest.json`
- `runs/final/parameter_input_sim/{run_id}/...`

## B2 파라미터 CSV

`configs/b2_parameter_sets.csv`를 수정한다.

```csv
parameter_id,D_det,alpha,G_ext,T_change_sec
```

- `D_det`: 응급차가 이 거리 안에 들어오면 제어 후보가 되는 거리(m).
- `alpha`: 응급차 통과 후 초록 유지 시간(s). 정수 초만 허용한다.
- `G_ext`: 초록 연장 상한(s). 정수 초만 허용한다.
- `T_change_sec`: green이 아닐 때 green 전환 요청 전 대기시간(s). 정수 초만 허용한다. 이 시간 중 기존 sequence가 green에 도달하면 `green_arrived_before_t_change_extension_count`로 집계되고, 최종 action은 `extend_green`이 된다.

## 핵심 지표

- `A_delay_sec`: B0/B2 응급차 통행시간에서 같은 route/repeat의 B00 통행시간을 뺀 값.
- `N_delay_sec`: 응급차 출동 이후 관측창의 전체 네트워크 비메인 도로 일반차 지연시간 평균. 출동 전부터 edge에 있던 차량은 출동 이후 겹친 체류분만 포함한다. 관측 종료 시점에 edge 위에 남은 기록은 종료 시점까지의 부분 체류시간으로 포함하고 `N_delay_censored_*` 컬럼에 별도 저장한다.
- `T_recovery_sec`: B0/B2에서 emergency route의 모든 TLS 교차로 대기행렬 회복시간 중 최댓값.
- `score_sec`: `3*A_delay_sec + 1*N_delay_sec + 1*T_recovery_sec`.
- `emergency_route_length_m`: 공식 보고용 route 길이. 서울역 직선 고정 경로는 외부 edge 합산 `2990.17m`다.
- `green_arrived_before_t_change_extension_count`: green이 아니어서 요청했지만 기존 phase sequence가 먼저 응급차 green에 도달해 연장으로 처리된 횟수다.

모든 시간 단위는 초(s), 소수 둘째자리다.

`result_score.csv`는 시행별 경량 score 입력 파일이며 `run_id,pipeline,mode,parameter_id,repeat_id,route_id,A_delay_sec,N_delay_sec,T_recovery_sec`만 저장한다.

## 관측 종료 시점

`timeout_steps=7200`은 최대 2시간 관측창이다. B0/B2의 primary metric은 모든 일반차가 도착할 때까지 기다리지 않고, 다음 조건을 만족하면 종료한다.

- emergency가 도착한다.
- emergency route의 모든 TLS 대기열이 출발 전 기준 이하로 회복된다.
- 회복 후 `recovery_buffer_sec`만큼 추가 관측한다. 기본값은 300초다.

실제 종료 시점은 `analysis_end_time_sec`, 종료 사유는 `analysis_stop_reason`에 저장한다. 일반차가 남아 있으면 `PASS_WITH_REMAINING_BACKGROUND`이며, 종료 시점 정체 지속 여부는 `congestion_valid_at_analysis_end`로 확인한다.

## 정체 유지 판정

종료 순간 속도 하나로 정체를 판정하지 않는다. `network_speed_pre_emergency_kmh`, `network_speed_during_response_kmh`, `network_speed_post_recovery_kmh`와 300초 rolling 평균을 함께 본다.

- `rolling_congestion_valid=True`: 300초 rolling 평균 속도가 관측창 동안 12~35km/h 범위다.
- `rolling_congestion_valid=False`: 결과는 `WARNING`이며 `rolling_congestion_reason`에 원인을 저장한다.
- `network_avg_speed_at_analysis_end_kmh`는 잔류 stuck 차량 진단용 보조 지표다.

## 성공 기준

- `sumo_exit_code=0`
- `emergency_departed=True`
- `emergency_arrived=True`
- `emergency_teleport=False`
- `route_error_count=0`
- `safety_violation_count=0`
- `emergency_stop_warning_count=0`
- `emergency_lane_connection_warning_count=0`
- 한 CSV 안에 `B00`, `B0`, `B2`가 함께 존재

background/general teleport는 `WARNING`, emergency teleport는 `FAIL`이다. 일반차가 남아 있는 것은 실패가 아니므로 `PASS_WITH_REMAINING_BACKGROUND`로 기록한다. emergency가 도착하지 못하면 `FAIL`이다.
