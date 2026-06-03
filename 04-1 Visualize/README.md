# 04-1 Visualize - Custom Destination B0 Animation

`04-1 Visualize`는 `04_visualize`를 복제한 뒤, 필동2가/회현동 custom destination B0 경로만 따로 애니메이션으로 만드는 폴더입니다.

원본 `04_visualize`는 수정하지 않습니다. 차량 표시는 04번 폴더의 FCD 애니메이션 방식과 동일하게 `L.circleMarker`, `bgLayer`, `bgByT`, `preferCanvas` 흐름을 사용합니다.

## 입력

- run pointer: `results/metrics/validated_custom_destination_b0/latest.json`
- route file: `data_prepared/validated/custom_routes/accepted_custom_routes.csv`
- FCD: latest run의 각 `run_dir/fcd.xml`

대상 row는 `experiment_results.csv`에서 아래 조건만 사용합니다.

- `mode == B0`
- `parameter_id == no_control`
- `route_id`가 `CUSTOM_`으로 시작

## 실행

```bash
.venv/bin/python "04-1 Visualize/animate_custom_destination_b0.py" \
  --latest-json results/metrics/validated_custom_destination_b0/latest.json \
  --accepted-routes data_prepared/validated/custom_routes/accepted_custom_routes.csv \
  --bg-radius-m 250
```

## 출력

- `results/html/custom_destination_b0_animation_index.html`
- `results/html/custom_destination_b0_animation_pildong.html`
- `results/html/custom_destination_b0_animation_hoehyeon.html`
- `results/html/custom_destination_b0_animation_pildong.json`
- `results/html/custom_destination_b0_animation_hoehyeon.json`

## 경로

- 필동2가 84-101
  - route id: `CUSTOM_JUNG_GU_PILDONG2_84_101`
  - target edge: `-273640070#3`
  - route length: `2335.78m`
- 회현동1가 147-23
  - route id: `CUSTOM_JUNG_GU_HOEHYEON1_147_23`
  - target edge: `-769488211`
  - route length: `2794.09m`

## 원칙

- B0만 시각화합니다.
- B0 재시뮬레이션은 하지 않고 기존 FCD를 사용합니다.
- 서울역 route length 상수는 사용하지 않고 accepted route CSV의 `route_length_m`을 사용합니다.
- custom `divIcon`, `vehicle-node`, `119` DOM marker는 만들지 않습니다.
