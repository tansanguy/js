# Step 16 B00/B0/B2 실험 러너

`02_simulation/run_b0_b1_b2_experiment.py`는 최종 B00/B0/B2 route-level 실험의 단일 진입점이다.

## 모드

- `B00`: 배경 차량 없이 신호등을 비활성화한 응급차 자유류 기준.
- `B0`: 600초 warm-up 후에도 지속되는 첨두시간 배경 수요, 신호 조작 없음.
- `B2`: B0과 같은 지속 수요, corridor priority 제어 적용.

`B1` task는 생성하지 않는다.

`B0`/`B2`는 응급차를 route XML에 정적 대기 차량으로 넣지 않고, 600초 warm-up 이후 TraCI로 동적 삽입한다. 정적 삽입 방식은 warm-up 정체가 이미 형성된 시작 edge에서 응급차가 장시간 pending 상태로 남을 수 있기 때문이다. `B00`은 배경 차량 없이 `tls.all-off=true`로 자유류 기준을 유지한다. `parameter_input_sim`은 `data_prepared/manual/seoul_station_manual_route.json`의 **서울역 직선 고정 경로**만 사용한다.

서울역 직선 고정 경로의 식별자는 `FIRE_TO_SEOUL_STATION`, 정책명은 `straight_seoul_station_fixed`, 도착 edge는 `619147738#0`이다. 공식 보고용 길이는 외부 edge 합산 `2990.17m`이다.

## 파이프라인

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B0 B2
```

## 출력

- `results/metrics/{output_prefix}/{run_id}/experiment_results.csv`
- `results/metrics/{output_prefix}/{run_id}/score_components.csv`
- `results/metrics/{output_prefix}/{run_id}/result_score.csv`
- `results/metrics/{output_prefix}/{run_id}/experiment_summary.json`
- `results/metrics/{output_prefix}/latest.json`
- `runs/final/{output_prefix}/{run_id}/{mode}/{parameter_id}/{repeat_id}/{route_id}/`

## 지표

- `A_delay_sec`: B00 대비 응급차 지연.
- `N_delay_sec`: 응급차 출동 이후 관측창의 전체 비메인 도로 일반차 지연. 출동 전부터 edge에 있던 차량도 출동 이후 겹친 체류분만 포함한다.
- `T_recovery_sec`: 소방서→서울역 첫 교차로 대기행렬 회복시간.
- `score_sec`: 세 지표의 합.
- `emergency_route_length_m`: 공식 보고용 외부 edge 합산 길이.
- `green_arrived_before_t_change_extension_count`: 요청 후 기존 phase sequence가 먼저 green에 도달해 extension으로 처리된 횟수.
- `network_speed_pre_emergency_kmh`, `network_speed_during_response_kmh`, `network_speed_post_recovery_kmh`: pre/during/post 구간 평균 속도.
- `rolling_congestion_valid`: 300초 rolling 평균 속도가 12~35km/h 범위 안에 유지되는지 여부.

`result_score.csv`는 시행별로 `A_delay_sec`, `N_delay_sec`, `T_recovery_sec`만 비교하기 위한 경량 산출물이다.
