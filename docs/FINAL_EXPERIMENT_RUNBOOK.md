# 최종 실험 실행 안내서

## 단일 진입점

최종 실험은 아래 러너만 사용한다.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py --manifest configs/final_experiment_manifest.json ...
```

Step9-Step15 스크립트는 준비, 진단, smoke 기록용이다. 최종 CSV 산출 명령으로 사용하지 않는다.

## venv 준비

```bash
cd /Users/junlee/Desktop/js
bash 00_setup/setup_venv.sh
source .venv/bin/activate
bash 00_setup/verify_env.sh
```

`verify_env.sh`는 `.venv/bin/python`을 자동으로 우선 사용한다. SUMO 실행 파일은 별도 시스템 설치가 필요하다.

## 모드

- `B00`: 배경 차량 없는 응급차 자유류 run.
- `B0`: 첨두시간 배경 수요 + 응급차, 신호 조작 없음.
- `B2`: B0과 같은 수요 + corridor priority 제어.

`B1`은 최종 비교 대상에서 제외한다.

## B2 안전 규칙

B2는 안전 규칙을 우선한다.

- `D_det` 안에서 이미 green이면 green 시간을 연장한다.
- green이 아니면 CSV의 `T_change_sec` 뒤 응급차 방향 green 전환을 요청한다.
- 노란불과 clearance phase를 건너뛰지 않는다.
- 직접 red→green 순간 점프는 금지하고, 전환 전 yellow/clearance 정리시간을 둔다.
- 보행자 최소 보행시간 보호를 위해 현재 phase를 단축하지 않는다.
- `alpha`, `G_ext`, `T_change_sec`는 정수 초만 허용한다.

이번 단계의 성공 기준은 B2가 B0보다 빠른지만이 아니라, emergency teleport, emergency stop warning, lane connection warning 없이 끝나는지다. 기본 선택값은 `D_det=500, alpha=5, G_ext=60, T_change_sec=10`이며, 후보 탐색 기록은 `configs/b2_stage1_parameter_sets.csv`와 `configs/b2_tchange_sweep_parameter_sets.csv`에 남긴다. Bayesian Optimization 전체 구현은 추후 범위다.

고정값:

- `w1=3.00`, `w2=1.00`, `w3=1.00`
- `score_sec = w1*A_delay_sec + w2*N_delay_sec + w3*T_recovery_sec`
- net은 `speed50` 파생 파일을 사용하고, 응급차는 `speedFactor=1.00`, `has.bluelight.device=false`로 둔다.

## 파라미터 입력 실험

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B0 B2 \
  --repeats 1 \
  --workers 1 \
  --timeout-steps 7200
```

출력:

- `results/metrics/parameter_input_sim.csv`
- `runs/final/parameter_input_sim/{run_id}/...`

## 최종 효과 검증 실험

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline final_effect_validation_sim \
  --modes B00 B0 B2 \
  --repeats 5 \
  --workers 4
```

출력:

- `results/metrics/final_effect_validation_sim.csv`
- `runs/final/final_effect_validation_sim/{run_id}/...`

## B2 파라미터 CSV

`configs/b2_parameter_sets.csv`를 수정한다.

```csv
parameter_id,D_det,alpha,G_ext
```

- `D_det`: 응급차가 이 거리 안에 들어오면 제어 후보가 되는 거리(m).
- `alpha`: 응급차 통과 후 초록 유지 시간(s). 정수 초만 허용한다.
- `G_ext`: 초록 연장 상한(s). 정수 초만 허용한다.

## 핵심 지표

- `A_delay_sec`: B0/B2 응급차 통행시간에서 같은 route/repeat의 B00 통행시간을 뺀 값.
- `N_delay_sec`: 전체 네트워크 비메인 도로 일반차 지연시간 평균. 완료된 차량-edge 기록만 primary metric에 사용하고, 미완료 active 기록 수와 비율은 별도 컬럼에 저장한다.
- `T_recovery_sec`: B0/B2에서 emergency route의 모든 TLS 교차로 대기행렬 회복시간 중 최댓값.
- `score_sec`: `3*A_delay_sec + 1*N_delay_sec + 1*T_recovery_sec`.

모든 시간 단위는 초(s), 소수 둘째자리다.

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

background/general teleport는 `WARNING`, emergency teleport는 `FAIL`이다. `timeout_steps=7200`은 2시간 관측창이며, 일반차가 남아 있는 것은 실패가 아니므로 `PASS_WITH_REMAINING_BACKGROUND`로 기록한다. emergency가 도착하지 못하면 `FAIL`이다.
