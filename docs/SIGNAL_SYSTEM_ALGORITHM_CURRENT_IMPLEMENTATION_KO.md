# 1.3 신호체계 알고리즘 현재 구현 상황 보고서

작성 기준: 2026-06-05, Compact V9 / B4 런타임 MVP 재실행 결과(`codex_caseb_spillback`)

## 한 줄 요약

현재 시뮬레이션은 사용자가 제시한 Stage 1, Stage 2, Stage 3 구조를 유지하되, 현장 historical DB가 없는 값은 B0, 즉 `B04 no-control` SUMO 실행에서 측정한 프록시 값으로 대체했습니다. 이 값은 현장 실측값이 아니라 `SUMO B0 measured proxy`입니다.

이번 수정으로 Stage 1 산출물에 B0 기반 Case B 후보와 queue calibration factor가 추가됐고, Stage 3 이벤트에는 `queue_source`, `case_b_source`, `tS_source`, `TA_case`, `case_b_mapping_status`, `case_b_segment_id`, `case_b_segment_queue_m_proxy`, `case_b_segment_fill`, `case_b_same_tls_policy`가 기록됩니다. 런타임 TraCI 값이 있으면 항상 TraCI가 우선이고, B0 값은 보정계수 또는 fallback으로만 사용됩니다.

## 현재 구현 위치

| 구분 | 현재 파일 |
| --- | --- |
| Stage 1 정적 산출물 | `data_prepared/compact_v9/b4_stage1/` |
| 실제 제어 런타임 | `09 Compact Corridor Baseline/b4_runtime.py` |
| B0/B4 비교 실행기 | `09 Compact Corridor Baseline/run_b0_b4_signal_pipeline.py` |
| 실행 결과 | `results/metrics/compact_v9_B4/experiment_results.csv` |
| B0 측정값 리뷰 HTML | `results/html/b4_ta_b0_measurement_review.html` |

## Stage 1: 초기화 구현 상태

Stage 1은 현재 실험용 경로와 네트워크에서 미리 계산된 정적 입력을 읽는 방식입니다. EV 경로는 중부소방서에서 서울역 앞까지 고정되어 있고, 약 3.03km / 61개 edge입니다.

| 항목 | 구현 상태 |
| --- | --- |
| EV 경로 | 중부소방서 → 서울역 앞 경로 |
| 제어 후보 | EV 경로 위 6개 movement |
| `q_avg`, `q_max`, `tQ_hist`, `lambda` | B0(B04 no-control) SUMO lane/edge data에서 측정 |
| `L`, `C` | SUMO 네트워크와 100m local storage 기준 프록시 |
| `queue_calibration_factor` | readiness queue / B0 measured queue를 CSV에 저장, runtime에는 clamp된 적용값 사용 |
| Case B 후보 | S7, S10, S11 CSV 후보를 route-span proxy로 기존 controllable movement에 연결해 `b4_case_b_candidates.csv/json`에 저장 |

### B0에서 측정한 주요 신호 파라미터

| movement | 구간 | q_avg | q_max | tQ_hist(s) | lambda(vph) | 제어 후보 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| B4_MOVEMENT_00 | S3 상행 | 0.063 | 2.508 | 1.672 | 316.078 | 아니오 |
| B4_MOVEMENT_01 | S6 상행 | 0.142 | 4.419 | 4.419 | 295.268 | 아니오 |
| B4_MOVEMENT_02 | S9 상행 | 0.953 | 11.222 | 7.481 | 169.007 | 예 |
| B4_MOVEMENT_03 | S9 상행 | 0.567 | 11.222 | 7.481 | 150.845 | 예 |
| B4_MOVEMENT_04 | S15 상행 | 0.714 | 8.549 | 5.699 | 170.558 | 예, 병목 모드 |
| B4_MOVEMENT_05 | S21 상행 | 1.467 | 13.738 | 9.158 | 118.271 | 예, 병목 모드 |

이 값들은 현장값이 아닙니다. 다만 같은 네트워크와 같은 수요를 무제어로 돌린 B0 결과에서 측정했기 때문에, 현재 시뮬레이션 내부에서는 일관성 있는 기준값입니다.

### Case B 후보 매핑

S7/S10/S11 자체를 새 신호로 만들지는 않았습니다. `B04_toegye_segment_edge_mapping.csv`의 상행 route edge span과 기존 `route_order_index`를 기준으로 가장 가까운 controllable movement에 보수적으로 연결했습니다.

| segment | bottleneck movement | upstream movement | mapping_status | same TLS | runtime |
| --- | --- | --- | --- | --- | --- |
| S7 | B4_MOVEMENT_02 | B4_MOVEMENT_01 | mapped_route_span_proxy | 아니오 | enabled |
| S10 | B4_MOVEMENT_03 | B4_MOVEMENT_02 | mapped_route_span_proxy | 예 | enabled |
| S11 | B4_MOVEMENT_04 | B4_MOVEMENT_03 | mapped_route_span_proxy | 아니오 | enabled |

Case B 후보 파일에는 `segment_edges`, `segment_lanes`, route span index, proxy edge gap, movement B0 prior, segment B0 prior가 같이 저장됩니다. Case B 전용 tau 기본값은 0.75이고, 기존 B4 smoke 파라미터의 `tau=0.50`이 들어와도 런타임 Case B 판정에는 `max(candidate.tau_default, params.tau)`가 적용됩니다.

## `local_fill_100m` 설명

`local_fill_100m`는 쉽게 말해 “정지선 가까운 100m 구간이 줄로 얼마나 찼는지”를 보는 값입니다.

```text
local_fill_100m = queue_m_proxy / 100m
```

예를 들어 `queue_m_proxy = 50m`이면 `local_fill_100m = 0.50`입니다. `queue_m_proxy = 135m`이면 `local_fill_100m = 1.35`가 됩니다. 1을 넘을 수 있는 이유는 실제 줄이 100m 기준 구간을 넘어 더 길게 잡힐 수 있기 때문입니다.

여기서 `queue_m_proxy`도 현장 실측 줄 길이가 아니라 SUMO의 lane/vehicle 상태에서 추정한 줄 길이입니다. 그래도 현재 알고리즘에서는 좋은 장점이 있습니다. 막힌 구간의 queue proxy가 70~90m 이상으로 나오는 경우, 100m를 분모로 쓰면 `0.70~0.90`처럼 바로 민감하게 잡히므로 EV가 가까워졌을 때 빠르게 반응할 수 있습니다.

다만 너무 민감해질 수 있으므로, 실제 선점은 `local_fill_100m`만으로 하지 않습니다. 현재는 다음 조건을 같이 봅니다.

1. `local_fill_100m >= 0.50` 또는 접근속도 `<= 15km/h`
2. `TA_proxy <= 0`
3. EV와 해당 movement 거리 `<= 250m`
4. 이미 SUMO net에 존재하는 안전한 target phase가 있을 것

이렇게 해서 짧은 순간의 작은 queue에 과도하게 반응하지 않도록 막고 있습니다.

## Stage 2: 합류 공간 확보

Stage 2의 목적은 EV가 본선에 합류하기 전에, 본선으로 계속 들어오는 일반차를 잠시 막아 합류 공간을 만드는 것입니다.

현재 계산값은 다음과 같습니다.

| 항목 | 값 |
| --- | ---: |
| 합류 제어 신호 | `COMPACT_V9_FIRE_STATION_ENTRY_TLS` |
| EV 출발 시각 | 600s |
| `D_merge` | 138.9m |
| `tE_merge` | 9.992806s |
| `L_merge` | 50.0m |
| `C_merge` | 7.692대 |
| `n_need` | 2.0대 |
| `n_occ_runtime` at hold | 0.0대 |
| `n_excess` | 0.0대 |
| `t_clear` | 0.0s |
| `tS_merge` | 5.0s |
| `T_hold` | 4.992806s |

계산은 다음과 같습니다.

```text
T_hold = tE_merge - t_clear - tS_merge
       = 9.992806 - 0.0 - 5.0
       = 4.992806s
```

해석하면, 이번 실행에서는 “EV 출발 약 5초 전부터 본선 유입 hold를 시작해도 합류 공간을 만들 수 있다”고 본 것입니다. SUMO는 1초 step으로 돌기 때문에 실제 이벤트는 596s에 시작됐습니다.

| 구분 | B0(B04 no-control) | B4_MVP_DEFAULT |
| --- | ---: | ---: |
| Stage 2 hold 시작 | 없음 | 596s |
| Stage 2 hold 해제 | 없음 | 617s |
| Stage 2 유효 hold | 없음 | 21.0s |
| hold 이벤트 수 | 0 | 1회 |

여기서 “Stage 2 유효 hold”는 신호를 hold phase로 바꾼 시각부터 EV가 합류 edge를 통과해 hold를 해제한 시각까지의 실제 지속시간입니다. B0는 아무 제어도 하지 않으므로 “없음”이고, B4는 596s부터 617s까지 21초간 일반차 유입을 막았습니다.

기존에는 고정 dispatch lead 때문에 565s부터 hold가 걸렸고, EV 도착 후 한 번 더 `entry_hold`가 찍히는 문제가 있었습니다. 현재는 `T_hold`를 직접 사용하고, 한 번 release된 뒤에는 Stage 2가 재진입하지 않도록 수정했습니다.

## Stage 3: 신호 선점

Stage 3는 EV가 본선에 합류한 뒤, EV 앞쪽의 신호를 미리 열어두는 단계입니다. 여기서 “선점”은 단순히 모든 신호를 초록불로 바꾸는 것이 아니라, 다음 조건을 만족할 때 해당 movement의 target green phase로 바꾸는 것을 말합니다.

1. EV 앞쪽에 있는 제어 후보인지 확인합니다.
2. 해당 접근부가 줄로 차 있거나 매우 느린지 확인합니다.
3. EV 도착 예상시간에서 신호 전환시간과 줄 방출시간을 뺀 `TA_proxy`를 계산합니다.
4. `TA_proxy <= 0`이면 “지금 열어야 EV가 도착할 때 비워진다”고 보고 선점합니다.
5. EV가 통과하면 원래 phase로 복구합니다.

이번 실행에서 실제 target green 전환은 5회 있었습니다.

| movement | 시각 | queue_m_proxy | local_fill_100m | TA_proxy(s) | 제어 모드 |
| --- | ---: | ---: | ---: | ---: | --- |
| B4_MOVEMENT_01 | 682s | 45.0m | 0.45 | -5.416 | 일반 |
| B4_MOVEMENT_02 | 756s | 90.0m | 0.90 | -0.123 | 병목 |
| B4_MOVEMENT_03 | 762s | 100.0m | 1.00 | -0.403 | 병목 |
| B4_MOVEMENT_04 | 1047s | 100.0m | 1.00 | -21.239 | 병목, Case B |
| B4_MOVEMENT_05 | 1185s | 100.0m | 1.00 | -0.194 | 병목 |

결과적으로 Stage 3 선점은 5회, 병목 모드는 4회 작동했습니다. 추가로 EV가 아직 통과하지 못했을 때 target green을 연장하거나, 같은 TLS 안에서 downstream movement를 잠깐 flush하는 보조 동작도 있었습니다.

이벤트 로그 기준 queue source는 `runtime_exact` 708건, `b0_calibrated` 24건, `b0_fallback` 81건입니다. 이번 smoke에서는 Case B 후보 segment lane까지 queue sampling 대상에 포함되어 런타임 queue lane 수가 89개에서 109개로 늘었습니다. 테스트에서는 stale/empty queue일 때 `tQ_hist_B0` fallback으로 TA를 계산하는 계약을 확인했습니다.

## TLS, linkIndex, phase 설명

SUMO에서 신호등 하나는 사람이 보는 “교차로 신호 하나”보다 더 복잡하게 표현됩니다.

`linkIndex`는 교차로 안의 개별 이동류입니다. 예를 들어 직진, 좌회전, 특정 차로의 연결이 각각 다른 linkIndex가 될 수 있습니다.

`phase`는 그 linkIndex들을 한꺼번에 묶은 신호 상태입니다. 즉, “이 linkIndex만 초록불로 켠다”가 아니라, SUMO에 이미 정의된 phase 하나를 선택하면 그 phase에 포함된 여러 linkIndex가 동시에 열리거나 닫힙니다.

그래서 현재 구현은 이론식처럼 “교차로 i만 독립적으로 열기”를 그대로 하지 않습니다. 대신 EV가 지나가야 하는 linkIndex가 포함된 기존 phase를 찾고, 그 phase로 바꿉니다. 이 조정은 알고리즘을 약하게 만든 것이 아니라, SUMO 신호체계 안에서 안전하게 실행되도록 바꾼 것입니다.

## Case B와 합류 제어의 관계

“EV 합류 자체를 신호로 직접 release하지 못한다”는 말은 Stage 2의 한계입니다. 이것이 곧 “Case B가 작동하지 않는다”는 뜻은 아닙니다.

구분하면 다음과 같습니다.

| 구분 | 의미 | 현재 상태 |
| --- | --- | --- |
| Stage 2 합류 제어 | EV가 본선에 들어갈 공간을 만들기 위해 일반차 유입을 hold | 작동함. 단, EV release 자체는 직접 제어하지 않음 |
| Case B / 병목 제어 | EV 앞쪽 도로가 막힌 경우, 병목 또는 하류 movement를 먼저 여는 제어 | S7/S10/S11 모두 route-span proxy로 매핑됨. runtime Case B source는 `runtime_tau_movement`와 `b0_prior`가 실제 기록됨 |

B4 런타임에는 Case B 후보가 매핑될 경우 `segment_queue_m_proxy / L >= tau`를 먼저 보고, segment queue가 없으면 movement queue를 보조 판정으로 사용합니다. runtime queue가 stale/empty이면 B0 prior를 fallback으로 씁니다. 테스트에서는 `runtime_tau_segment`, `runtime_tau_movement`, `b0_prior`, tau 미충족 시 Case A 전환을 각각 확인했습니다.

실제 smoke 이벤트 로그에서는 `case_b_source`가 `not_case_b` 557건, `runtime_tau_movement` 225건, `b0_prior` 101건으로 기록됐습니다. segment runtime queue는 실제 교통상 tau를 넘지 않아 `runtime_tau_segment`가 smoke에서 발동하지 않았고, 이 경우는 synthetic runtime test를 acceptance 근거로 둡니다.

같은 TLS에 묶인 S10은 병목과 upstream phase가 다를 수 있습니다. 이때 같은 step에서 둘 다 바꾸지 않고 병목 phase를 우선합니다. 실제 smoke에는 `case_b_same_tls_policy=bottleneck_first_defer_upstream_same_tls`가 61건 기록됐고, unit test에서는 upstream 즉시 phase change 대신 `case_b_same_tls_deferred` 이벤트가 남는 것을 확인했습니다.

사용자 의견처럼 막힌 구간의 queue proxy가 70m 이상으로 나오는 상황에서는 100m 분모를 쓰는 방식이 충분히 합리적입니다. 현재 실행에서도 60m, 67.5m, 135m, 150m, 540m queue proxy에서 제어가 발생했습니다. 다만 100m 분모는 민감하므로, `TA_proxy`, EV 거리, 저속 조건을 함께 두는 현재 방식이 더 안전합니다.

## 단일 파라미터 재실행 결과

이번 결과는 `B4_MVP_DEFAULT`를 seed=1, repeat=1로 1회 실행한 값입니다. 통계적 결론이 아니라 현재 구현 확인 결과입니다.

| 구분 | B0(B04 no-control) | B4_MVP_DEFAULT |
| --- | ---: | ---: |
| EV 통행시간 | 926.0s | 624.0s |
| 자유류 기준 시간 | 217.98s | 217.98s |
| EV 지체 | 708.02s | 406.02s |
| B4 - B0 | - | -302.0s |
| 배경차량 평균 통행시간 | 172.51s | 181.70s |
| 배경차량 평균 지체 | 124.40s | 132.14s |
| Stage 2 유효 hold | 없음 | 21.0s |
| Stage 3 선점 | 없음 | 5회 |
| 병목 모드 작동 | 없음 | 4회 |
| EV 도착 | 예 | 예 |
| teleport | 아니오 | 아니오 |

## 현재 남아 있는 한계

1. historical DB는 아직 없고, `SUMO B0 measured proxy`를 사용했습니다.
2. B4는 현재 `B4_MVP_DEFAULT` 1개만 실행했습니다. 다중 seed, 반복 실행, 출발시각 변화 검증은 아직 필요합니다.
3. Stage 2는 일반차 유입을 hold해 합류 공간을 만드는 방식입니다. EV release movement 자체를 직접 제어하지는 못합니다.
4. `local_fill_100m`는 반응성이 좋은 대신 민감합니다. 그래서 지금처럼 `TA_proxy`, 거리 제한, 저속 조건과 함께 쓰는 것이 안전합니다.
5. SUMO 신호 phase 경고가 일부 남아 있습니다. 이번 실행은 완료됐지만, 최종 검증 전에는 네트워크 phase 정의를 추가 점검하는 편이 좋습니다.

## 정리

현재 B4 런타임 MVP는 B0 측정 프록시 기반으로 합류부 hold와 전방 신호 선점을 수행합니다. 이번 수정 후 Stage 2는 연구 초안의 `T_hold=4.992806s`를 직접 사용했고, 실제 hold는 596s부터 617s까지 21초간 유지됐습니다.

단일 실행에서는 EV 통행시간이 B0 926초에서 B4 624초로 줄었습니다. 다만 이 결과는 seed=1, repeat=1의 구현 확인 결과이므로, 최종 성능 주장에는 반복 실험과 파라미터 검증이 추가로 필요합니다.
