# Validation Method

이 문서는 B0 현실 recall 검증을 어떤 절차로 수행했는지만 정리한다. 결과 해석, 개선안, 수요/신호 보정 판단은 포함하지 않는다.

## 검증 대상

- 기준 현실 데이터: `toegye_ro_mainstream_segments_english.csv`
- 대상 구간: 퇴계로 본선 `S1`-`S22`, 상행/하행 양방향
- 대상 시뮬레이션: B0 baseline만 사용
  - `mode == B0`
  - `parameter_id == no_control`
  - 실패 상태가 아닌 run row
- 대상 네트워크: manifest의 `active_net`
- 대상 시뮬레이션 산출물: B0 run의 `edgeData.xml`

기본 실행 형태:

```bash
.venv/bin/python "01-1 Validation/validate_b0_reality_recall.py" \
  --reference-csv toegye_ro_mainstream_segments_english.csv \
  --manifest configs/final_experiment_manifest.json \
  --results-csv auto
```

`--results-csv auto`는 `results/metrics/**/experiment_results.csv` 중 사용 가능한 최신 B0/no_control row를 찾는다. validated pipeline의 scale sweep, congestion mapping, TLS/boundary sweep도 이 validator를 호출해 같은 방식으로 현실 recall을 산출한다.

## 입력 파싱

Reference CSV는 `utf-8-sig`로 읽고 필수 컬럼을 검증한다.

주요 현실 기준값:

- geometry: `start_latitude`, `start_longitude`, `end_latitude`, `end_longitude`
- segment length: `segment_length_m`
- lane target: `upbound_lanes_to_seoul_station`, `downbound_lanes_to_seongdong_high_school`
- speed target: `avg_speed_kmh_upbound`, `avg_speed_kmh_downbound`
- travel-time target: `travel_time_s_upbound`, `travel_time_s_downbound`
- demand target: `peak_hour_volume_veh_per_h_reference`

SUMO net은 passenger edge만 feature로 변환한다. 각 edge에서 shape, length, lane count, speed limit, heading을 읽는다. 좌표 변환은 SUMO net의 `convertLonLat2XY`를 우선 사용하고, 필요한 경우 UTM fallback을 사용한다.

`edgeData.xml`에서는 interval begin/end, edge별 `entered`, `left`, `departed`, `arrived`, `speed`, `sampledSeconds`를 읽는다. 통과량 recall에는 `max(entered, left, departed, arrived)`를 screenline count로 사용한다.

## 매칭 방식

Map recall은 현실 segment 선분을 10m 간격으로 샘플링한다. 각 sample point에서 가장 가까운 passenger edge가 35m 이내이면 covered로 본다.

방향별 demand/speed/lane recall은 segment heading과 edge heading을 같이 본다.

- 방향별 후보 edge는 segment 방향과 heading tolerance 안에 있고, 거리 기준을 만족하는 edge다.
- 상행은 CSV의 upbound 기준값을 사용한다.
- 하행은 CSV의 downbound 기준값을 사용한다.
- 한 segment/direction에 여러 edge가 매칭될 수 있으며, lane과 speed는 매칭 edge 집합 전체로 평가한다.

## 판정 기준

Map recall:

- segment별 recall = covered sample 수 / 전체 sample 수
- corridor recall = segment length 가중 평균
- PASS 조건:
  - corridor recall `>= 0.95`
  - 모든 segment recall `>= 0.80`

Lane recall:

- segment-direction 단위로 현실 lane target과 매칭 edge lane count를 비교한다.
- 대표 lane은 mode/median으로 판단한다.
- 대표 lane이 현실 target과 일치하면 `PASS`
- 대표 lane은 다르지만 매칭 edge의 max lane이 현실 target과 같으면 `WARN`
- 그 외 `FAIL`
- 전체 lane PASS 조건: `(PASS + WARN) / 전체 row >= 0.90`
- strict lane recall은 `PASS / 전체 row`로 별도 기록한다.

Demand recall:

- CSV의 `peak_hour_volume_veh_per_h_reference`는 방향별 veh/h로 해석한다.
- raw reference count는 edgeData interval duration만 반영한다.
- scaled reference count는 manifest의 `warmup_sec`, `warmup_scale`, `sustain_scale`과 edgeData interval duration을 반영한다.
- 기본 gate는 scaled recall을 사용한다.
- GEH도 scaled reference count 기준으로 계산한다.
- PASS 조건:
  - segment-direction별 scaled recall 중앙값이 `0.70`-`1.30`
  - GEH `PASS/WARN` 비율 `>= 0.80`
- WARN 조건:
  - scaled recall 중앙값이 `0.50`-`1.50`
  - GEH `PASS/WARN` 비율 `>= 0.80`
- 그 외 `FAIL`

Speed/travel-time recall:

- segment-direction 단위로 현실 평균속도와 edgeData 기반 simulated speed를 비교한다.
- simulated speed는 매칭 edge의 edgeData speed를 sampledSeconds 또는 edge length로 가중 평균한다.
- simulated travel time은 `segment_length_m / simulated_speed`로 계산한다.
- PASS 조건: speed MAE `<= 5 km/h`
- WARN 조건: speed MAE `<= 8 km/h`
- 그 외 `FAIL`

Edge-speed recall:

- segment-direction에 매칭된 각 edge를 개별 평가한다.
- edgeData speed와 해당 segment-direction의 현실 평균속도를 비교한다.
- `speed_error > 8 km/h`는 `over_open_speed`
- `speed_error < -8 km/h`는 `under_speed`
- `abs(speed_error) > 5 km/h`는 `speed_error_warn_range`
- edge-speed 전체는 실패 edge가 있거나 MAE가 `8 km/h`를 넘으면 `FAIL`, warn edge가 있거나 MAE가 `5 km/h`를 넘으면 `WARN`이다.

Overall status:

- `map_status`, `lane_status`, `demand_status`, `speed_status`, `edge_speed_status` 중 가장 나쁜 상태를 사용한다.
- 우선순위는 `PASS < WARN < FAIL`이다.

## 산출물

기본 산출물 위치:

```text
results/metrics/validation_b0/{run_id}/
```

validated pipeline에서 호출한 경우에는 호출 단계별 output root 아래에 같은 파일명이 생성된다.

생성 파일:

- `b0_map_recall.csv`
- `b0_lane_recall.csv`
- `b0_demand_recall.csv`
- `b0_speed_travel_time_recall.csv`
- `b0_edge_speed_recall.csv`
- `b0_demand_adjustment_recommendations.csv`
- `validation_summary.json`

`validation_summary.json`에는 입력 경로, B0 row, edgeData interval, manifest scale, 각 status, 각 metric summary, 산출물 경로가 기록된다.

## 수요 조정 추천

검증이 `WARN` 또는 `FAIL`이면 추천 CSV/JSON이 생성된다. 추천은 보고 전용이며 route XML이나 SUMO config를 생성하지 않는다.

- global multiplier = `median(scaled_reference_count / observed_count)`
- recommended warmup/sustain scale = 기존 manifest scale에 global multiplier를 곱한 값
- segment-direction multiplier = `scaled_reference_count / observed_count`
- observed count가 0이면 `missing_flow_or_mapping`
- 부족 구간은 `increase_demand`
- 과잉 구간은 `decrease_demand`

## Repaired/Validated Map 검증

lane-repaired net은 별도 map validation을 수행했다.

```bash
.venv/bin/python "01-2 Validated/step04_validate_repaired_map.py"
```

이 단계는 다음만 확인한다.

- repaired net의 lane recall
- 서울역 synthetic route 연결성
- `tlLogic` 존재 여부
- TLS connection 존재 여부

이 map validation은 B0 edgeData 기반 수요/속도 recall을 보지 않는다. B0 현실 recall은 항상 `01-1 Validation/validate_b0_reality_recall.py`로 수행한다.
