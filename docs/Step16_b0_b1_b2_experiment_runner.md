# Step 16 B00/B0/B2 실험 러너

`02_simulation/run_b0_b1_b2_experiment.py`는 최종 B00/B0/B2 route-level 실험의 단일 진입점이다.

## 모드

- `B00`: 배경 차량 없는 응급차 자유류 기준.
- `B0`: 첨두시간 배경 수요, 신호 조작 없음.
- `B2`: B0과 같은 수요, corridor priority 제어 적용.

`B1` task는 생성하지 않는다.

## 파이프라인

파라미터 입력 실험:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B0 B2
```

최종 효과 검증:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline final_effect_validation_sim \
  --modes B00 B0 B2
```

## 출력

- `results/metrics/parameter_input_sim.csv`
- `results/metrics/final_effect_validation_sim.csv`
- `runs/final/{pipeline}/{mode}/{parameter_id}/{repeat_id}/{route_id}/`

## 지표

- `A_delay_sec`: B00 대비 응급차 지연.
- `N_delay_sec`: 전체 비메인 도로 일반차 지연.
- `T_recovery_sec`: 소방서→서울역 첫 교차로 대기행렬 회복시간.
- `score_sec`: 세 지표의 합.
