# 10-1 Final Visualization

10-1은 10번 최종 목적지 검증 결과에 붙이는 제출용 진행 애니메이션 산출물입니다.
기준 UI는 `/Users/junlee/Downloads/taehoon_cma_r45_001_b04_b4_progress_animation.html`의
Leaflet dual-map 방식입니다.

신호/알고리즘 사실과 발표용 연출의 경계는 `TRUTH_VS_PRESENTATION.md`를 기준으로 합니다.

## 목표

- 10번 최종 run의 B04/B4 paired repeat를 한 화면에서 비교한다.
- 응급차 진행, 주변 차량, 발표용으로 보정한 신호 현시를 같은 시간축으로 재생한다.
- 신호 조작이 눈에 띄도록 신호등 아이콘과 활성 테두리를 강화한다.

## 10-1 전용 입력

10-1 산출물은 원본 9번/`data_prepared` 입력을 직접 수정하지 않습니다. 대신 아래 파일을 10-1 폴더 안에 복사해
presentation-owned 입력으로 사용합니다.

- `10-1_jungbu_compact_v9_B04_global_reality_s1forced_presentation.net.xml`
- `10-1_background_routes_compact_v9_B04_ad_stage23_trigger_presentation.rou.xml`
- `10-1_firetruck_to_seoul_station_front.rou.xml`
- `10-1_presentation_inputs.json`

현재 최종 HTML은 raw TLS replay가 아니라 `10-1_presentation_inputs.json`의 `10-1_suitable_signal_system`을 사용합니다.
일반차는 이 신호체계를 지키는 presentation demand 차량이며, green이어도 앞 구간 포화가 있으면
`green_downstream_queue`로 정지합니다.

## 수정 방침

1. 쌍둥이 신호등 제거
   - 표시 단위는 raw node나 모든 controlled link가 아니라 `tls_id + 진행방향 stopline cluster`입니다.
   - 같은 TLS/같은 진행거리에서 12m 이내에 겹치는 아이콘은 하나로 합칩니다.
   - 가장 가까운 신호쌍이 35m 이내이면 발표용 HTML 데이터에서만 하나를 삭제합니다.
   - 원본 네트워크, route, simulation output은 수정하지 않습니다.

2. `초록 -> 노랑 -> 초록` 제거
   - 시각화 색은 raw phase 색이 아니라 EV-facing permission state로 재해석합니다.
   - 짧은 `green-yellow-green` 클리어런스 깜빡임은 기본 3초 이하이면 green으로 흡수합니다.
   - 모든 표시 신호는 `G > Y > R + Rall > G` 순서로 보이도록 상태기계를 통과합니다.

3. 윗차선/아래차선 차량수 통일
   - 개별 background vehicle 점은 메인 지도에서 기본 표시하지 않습니다.
   - EV-facing 신호와 raw 일반 차량을 같이 움직이면 신호 위반처럼 보이기 때문입니다.
   - 대신 red/yellow/Rall 구간은 stopline 앞 정체 리본과 대기 차량 점을 표시합니다.
   - green 구간은 통행 리본과 진행 차량 점을 표시하되, 정지선을 넘는 연출은 만들지 않습니다.
   - 점 위치는 EV route 거리 좌표에서 계산해서 랜덤 순간이동처럼 보이지 않게 합니다.
   - 지도 위 라벨로 `정지선 뒤 대기`, `일반차 진행`, `신당역 유입 차단`을 직접 표시합니다.
   - 일반차 점의 이동 속도는 FCD background 차량을 EV 경로 주변으로 투영한 평균 속도에 비례합니다.
   - EV 앞 흐름과 EV 뒤 후속 흐름을 분리해서 표시합니다.
   - 단, 표시 신호가 red/yellow/Rall이면 실제 평균 속도가 높아도 움직이는 점을 그리지 않고 대기열로 표시합니다.
   - green이어도 EV가 정지 중이면 자유 진행이 아니라 `교차로 차량 배출 중`과 `EV 뒤 대기열`로 표시합니다.

3-1. EV 정지 이유 표시
   - EV 속도가 2km/h 이하이면 패널의 `EV 상태`에 정지 이유를 표시합니다.
   - red/yellow/Rall 정지는 다음 신호/clearance 대기로 표시합니다.
   - B4 green 상태에서 정지하면 우선신호는 열렸지만 앞차/교차로 정리 중인 장면으로 표시합니다.
   - EV가 정지한 상태에서는 일반차 문구가 `진행 흐름`으로 나오지 않도록 `교차로 차량 배출 중`이나
     `전방 정체 정리 중`으로 바꿉니다.

4. 신호 조작 가시성 강화
   - 신호등 아이콘을 기존보다 크게 그립니다.
   - red/yellow/green 채도와 glow를 높입니다.
   - 모든 신호등 아이콘은 항상 불투명하게 표시합니다.
   - 다음 진행방향 신호등만 노란색 테두리를 켭니다. 이 테두리는 제어 활성 상태와 독립입니다.

5. 중간에 붙은 신호등 2개 확인
   - `*_diagnostics.json`에 close pair 진단을 기록합니다.
   - 같은 TLS/같은 stopline이면 collapse 대상입니다.
   - 실제 인접 제어기여도 발표용으로는 하나를 삭제해서 완벽하게 정리된 것처럼 보정합니다.

6. 차량 경로가 잠깐 뒤틀리는 현상
   - 1차 구현에서는 FCD 좌표를 그대로 쓰되, 비현실적 점프는 이전/다음 점 보간으로 완화합니다.
   - 근본 해결은 lane/edge shape와 FCD edge 매핑 재검증이 필요합니다.

7. 노란불이 안 보이는 현상
   - yellow 상태를 CSS와 state timeline 양쪽에서 보장합니다.
   - raw TLS dump에 yellow가 없어도 발표용 상태기계에서 green-to-red 전환 시 yellow를 삽입합니다.

## 사용

### 전체 파이프라인

아래 명령은 현재 검증 run의 FCD/TLS/event 산출물을 읽어 10-1 최종 HTML, 데이터, 검증 리포트,
파이프라인 summary를 생성합니다. `--skip-validation`을 빼면 10번 최종 검증 smoke를 먼저 실행한 뒤
같은 10-1 빌더를 호출합니다.

```bash
python3 "10-1 Final Visualization/run_final_visualization_pipeline.py" \
  --skip-validation \
  --run-id final_destination_validation_bo_best_20260608_viz_pass_1688 \
  --route-id FINAL_DEST_ER_ACC_006 \
  --repeat-id repeat_001
```

기본값은 대표 route 1개, `repeat_001`, `repeats=1`, `workers=1`, `final-selection-count=1`입니다.
즉 run id만 바꿔도 새 smoke와 HTML이 같이 만들어집니다.

이미 FCD가 있는 run에서 HTML만 다시 만들려면:

```bash
python3 "10-1 Final Visualization/run_final_visualization_pipeline.py" \
  --run-id final_viz_smoke_bo_best_20260608 \
  --route-id FINAL_DEST_ER_ACC_006 \
  --repeat-id repeat_001 \
  --skip-validation
```

최종 산출물은 기본적으로 `10-1 Final Visualization/` 아래에 고정 이름으로 생성됩니다.

- `seoul_station_fire_station_presentation.html`
- `seoul_station_fire_station_presentation_data.json`
- `seoul_station_fire_station_presentation_validation_report.json`
- `seoul_station_fire_station_presentation_pipeline_summary.json`

### 단일 실행 측정 replay 파이프라인

최종 납득용 화면은 아래 흐름으로 만듭니다.

1. 지금 최종 경로로 여러 repeat을 실행한다.
2. 10-1이 B04/B4 paired repeat 중 시각화 효과가 좋은 실행을 고른다.
3. 그 repeat의 EV 출발시각을 `--depart-min == --depart-max`로 고정하고, repeat 1개만 다시 실행한다.
4. 재실행 때 `fcd.xml`, `tls_states.csv`, `signal_events.csv`를 모두 켠다.
5. 그 측정값만 읽어서 `seoul_station_fire_station_measured_replay.html`을 만든다.

이 파이프라인의 원칙은 기존 발표용 scene graph와 다릅니다. 신호체계, EV, 일반차, 큐 길이,
알고리즘 반응은 측정 산출물이 source-of-truth입니다. HTML은 FCD sample 사이 보간과 Canvas draw만 하고,
브라우저에서 차량 생성, 신호 판단, 큐 판단을 하지 않습니다.

먼저 기존 최종 run에서 후보를 고릅니다.

```bash
python3 "10-1 Final Visualization/prepare_measured_replay_pipeline.py" \
  --source-run-id <FINAL_RUN_ID> \
  --route-id FINAL_DEST_ER_ACC_006
```

결과는 아래 plan에 저장됩니다.

- `10-1 Final Visualization/10-1_measured_replay_plan.json`

`selection.strict_winner`가 `true`인 경우만 최종 시각화 후보입니다. `false`이면 plan은 배선 확인용으로만 쓰고,
실제 측정 재실행과 scene build는 기본적으로 막습니다. 예를 들어 현재 로컬의
`final_viz_tls_smoke_bo_20260609_r5`는 B4가 실패한 repeat이 선택되어 `strict_winner=false`입니다.
이 run은 최종본 source로 쓰면 안 됩니다.

strict winner plan이 나온 뒤에는 한 번에 재실행과 scene build를 수행합니다.

```bash
python3 "10-1 Final Visualization/prepare_measured_replay_pipeline.py" \
  --source-run-id <FINAL_RUN_ID> \
  --route-id FINAL_DEST_ER_ACC_006 \
  --execute-measured-run \
  --build-scene
```

또는 `10-1_measured_replay_plan.json` 안의 `commands.measured_run`,
`commands.build_scene`을 순서대로 실행해도 됩니다.

산출물:

- `seoul_station_fire_station_measured_replay.html`
- `seoul_station_fire_station_measured_replay_data.json`
- `seoul_station_fire_station_measured_replay_manifest.json`

measured replay HTML은 sibling JSON을 `fetch()`로 읽습니다. 브라우저 확인은 로컬 HTTP로 엽니다.

```bash
cd "10-1 Final Visualization"
python3 -m http.server 8765
```

브라우저 URL:

- `http://localhost:8765/seoul_station_fire_station_measured_replay.html`

### HTML 생성기만 직접 실행

최종 run 디렉터리에 `fcd.xml`이 있어야 합니다. `tls_states.csv`가 있으면 우선 사용하고,
없으면 route connection에서 진행방향 신호 위치를 뽑아 발표용 타임라인을 합성합니다.

```bash
python3 "10-1 Final Visualization/build_final_progress_animation.py" \
  --run-id final_destination_validation_bo_best_20260608 \
  --route-id FINAL_DEST_ER_ACC_006 \
  --repeat-id repeat_001
```

산출물:

- `results/html/final_destination_validation_bo_best_20260608_FINAL_DEST_ER_ACC_006_repeat_001_progress.html`
- `results/html/final_destination_validation_bo_best_20260608_FINAL_DEST_ER_ACC_006_repeat_001_progress_data.json`
- `results/html/final_destination_validation_bo_best_20260608_FINAL_DEST_ER_ACC_006_repeat_001_progress_diagnostics.json`

현재 로컬 `main`에는 기존 final metrics만 있고 FCD가 없는 run이 있을 수 있습니다.
이 경우 선택한 대표 route/repeat만 `--emit-fcd`로 smoke 재실행한 뒤 생성합니다.

## FCD 보강 실행 메모

10번 러너는 이미 `--emit-fcd`를 받습니다. 최종 제출용 애니메이션은 전체 30-repeat가 아니라 선택한
대표 repeat 1개만 FCD 포함으로 다시 만드는 것이 파일 크기와 검증 비용 면에서 낫습니다.

최근 Bayesian 최적해 smoke 예:

```bash
python3 "10 Final Destination Validation/final_destination_validation.py" \
  --phase final \
  --run-id final_viz_smoke_bo_best_20260608 \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_bo_fixed_v2_n1_m50_t6_20260608/all_evaluations.csv" \
  --theta-method BO \
  --selected-routes FINAL_DEST_ER_ACC_006 \
  --final-selection-count 1 \
  --repeats 1 \
  --workers 1 \
  --emit-fcd
```
