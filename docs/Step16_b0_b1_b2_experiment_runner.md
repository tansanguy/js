# Step 16 B00/B0/B2 실험 러너

`02_simulation/run_b0_b1_b2_experiment.py`는 최종 B00/B0/B2 route-level 실험의 단일 진입점이다.

## 모드

- `B00`: 배경 차량 없이 신호등을 비활성화한 응급차 자유류 기준.
- `B0`: 600초 warm-up 후에도 지속되는 첨두시간 배경 수요, 신호 조작 없음.
- `B2`: B0과 같은 지속 수요, corridor priority 제어 적용.

`B1` task는 생성하지 않는다.

`B0`/`B2`는 응급차를 route XML에 정적 대기 차량으로 넣지 않고, 600초 warm-up 이후 TraCI로 동적 삽입한다. 정적 삽입 방식은 warm-up 정체가 이미 형성된 시작 edge에서 응급차가 장시간 pending 상태로 남을 수 있기 때문이다. `B00`은 배경 차량 없이 `tls.all-off=true`로 자유류 기준을 유지한다. `parameter_input_sim`은 `data_prepared/manual/seoul_station_manual_route.json`의 고정 직선 서울역 경로만 사용한다.

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

- `results/metrics/{output_prefix}/{run_id}/experiment_results.csv`
- `results/metrics/{output_prefix}/{run_id}/score_components.csv`
- `results/metrics/{output_prefix}/{run_id}/experiment_summary.json`
- `results/metrics/{output_prefix}/latest.json`
- `runs/final/{output_prefix}/{run_id}/{mode}/{parameter_id}/{repeat_id}/{route_id}/`

## 지표

- `A_delay_sec`: B00 대비 응급차 지연.
- `N_delay_sec`: 응급차 출동 이후 관측창의 전체 비메인 도로 일반차 지연. 출동 전부터 edge에 있던 차량도 출동 이후 겹친 체류분만 포함한다.
- `T_recovery_sec`: 소방서→서울역 첫 교차로 대기행렬 회복시간.
- `score_sec`: 세 지표의 합.
- `network_speed_pre_emergency_kmh`, `network_speed_during_response_kmh`, `network_speed_post_recovery_kmh`: pre/during/post 구간 평균 속도.
- `rolling_congestion_valid`: 300초 rolling 평균 속도가 12~35km/h 범위 안에 유지되는지 여부.
