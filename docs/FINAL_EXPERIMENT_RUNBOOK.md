# 최종 실험 실행 안내서

## 단일 진입점

최종 실험은 아래 러너만 사용한다.

```bash
python3 02_simulation/run_b0_b1_b2_experiment.py --manifest configs/final_experiment_manifest.json ...
```

Step9-Step15 스크립트는 준비, 진단, smoke 기록용이다. 최종 CSV 산출 명령으로 사용하지 않는다.

## 모드

- `B00`: 배경 차량 없는 응급차 자유류 run.
- `B0`: 첨두시간 배경 수요 + 응급차, 신호 조작 없음.
- `B2`: B0과 같은 수요 + corridor priority 제어.

`B1`은 최종 비교 대상에서 제외한다.

## 파라미터 입력 실험

```bash
python3 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B0 B2 \
  --repeats 1 \
  --workers 1
```

출력:

- `results/metrics/parameter_input_sim.csv`
- `runs/final/parameter_input_sim/...`

## 최종 효과 검증 실험

```bash
python3 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline final_effect_validation_sim \
  --modes B00 B0 B2 \
  --repeats 5 \
  --workers 4
```

출력:

- `results/metrics/final_effect_validation_sim.csv`
- `runs/final/final_effect_validation_sim/...`

## B2 파라미터 CSV

`configs/b2_parameter_sets.csv`를 수정한다.

```csv
parameter_id,D_det,alpha,G_ext
```

- `D_det`: 응급차가 이 거리 안에 들어오면 제어 후보가 되는 거리(m).
- `alpha`: 응급차 통과 후 초록 유지 시간(s).
- `G_ext`: 초록 연장 상한(s).

## 핵심 지표

- `A_delay_sec`: B0/B2 응급차 통행시간에서 같은 route/repeat의 B00 통행시간을 뺀 값.
- `N_delay_sec`: 전체 네트워크 비메인 도로 일반차 지연시간 평균.
- `T_recovery_sec`: B2에서 소방서→서울역 첫 교차로 대기행렬 회복시간.
- `score_sec`: 세 지표의 합.

모든 시간 단위는 초(s), 소수 둘째자리다.

## 성공 기준

- `sumo_exit_code=0`
- `emergency_departed=True`
- `emergency_arrived=True`
- `emergency_teleport=False`
- `route_error_count=0`
- 한 CSV 안에 `B00`, `B0`, `B2`가 함께 존재

background/general teleport는 `WARNING`, emergency teleport는 `FAIL`이다.
