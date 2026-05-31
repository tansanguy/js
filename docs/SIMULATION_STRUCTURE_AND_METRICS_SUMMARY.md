# B2 최적화 결과 CSV 검토 요약

이 문서는 결과 CSV만 받은 사람이 B2 우선신호 최적화가 의도한 규칙대로 측정되고 평가됐는지 확인하기 위한 요약이다. 실행 환경, 지도 구축, 파일 생성 절차는 제외하고 `측정값`, `측정 과정`, `Bayesian Optimization 병렬 평가 구조`, `최종 시뮬레이션에서 결정할 것`, `해석 조언`만 정리한다.

## 1. 결과 CSV에서 먼저 볼 값

| 구분 | 컬럼 | 의미 | 판단 기준 |
| --- | --- | --- | --- |
| 상태 | `final_status` | 해당 row가 정상 비교 가능한지 | `FAIL`은 후보 판단에서 제외한다. `WARNING`은 사유를 확인한다. |
| 응급차 효과 | `A_delay_sec` | 자유류 대비 응급차 지연 | 낮을수록 좋다. B2가 B0보다 줄었는지 본다. |
| 일반차 영향 | `N_delay_sec` | 비메인 도로 일반차 평균 지연 | 낮을수록 좋다. B2가 응급차만 빠르게 하고 일반차를 크게 악화시키지 않았는지 본다. |
| 회복 부담 | `T_recovery_sec` | 응급차 통과 후 TLS queue 회복시간 | 낮을수록 좋다. 특정 신호에서 회복이 길어졌는지 확인한다. |
| 기본 점수 | `score_sec` | 연구 기본 score | `3*A_delay_sec + N_delay_sec + T_recovery_sec` |
| BO 점수 | `bo_score_sec` | Bayesian Optimization target | 실제 최적화가 낮추는 값이다. |
| 신호 부담 | `signal_burden_penalty_sec` | 신호 개입 penalty | `score_sec`는 좋은데 이 값이 크면 과한 제어일 수 있다. |
| 개입량 | `realized_extension_sec` | 실제 green 연장 부담 | 낮을수록 신호 부담이 작다. |
| 전환 횟수 | `phase_switch_count` | 직접 green 전환 횟수 | 많을수록 신호 운영 부담이 크다. |
| 정체 조건 | `rolling_congestion_valid` | 비교 가능한 정체가 유지됐는지 | `False`면 score 비교 신뢰도를 낮춰 봐야 한다. |

## 2. 측정값이 만들어지는 과정

| 측정값 | 계산 과정 | 확인할 점 |
| --- | --- | --- |
| `emergency_travel_time_sec` | SUMO `tripinfo`의 응급차 주행시간을 읽는다. | 응급차가 실제 도착했는지 `emergency_arrived=True`를 먼저 확인한다. |
| `A_delay_sec` | 같은 `route_id`와 `repeat_id`의 B00 자유류 주행시간을 B0/B2 주행시간에서 뺀다. | B00 row가 같은 조건에 존재해야 한다. |
| `N_delay_sec` | 응급차 출동 이후 비-main/internal 제외 edge에서 일반차 vehicle-edge별 실제 체류시간과 자유류 시간을 비교해 평균낸다. | 큰 값이면 응급차 우선 제어가 주변 일반차 지연을 키웠을 가능성이 있다. |
| `T_recovery_sec` | route TLS별 queue가 응급차 통과 후 출발 전 기준 이하로 돌아오는 시간을 계산하고 그 최댓값을 쓴다. | 큰 값이면 `queue_recovery_by_tls.csv`에서 어떤 TLS가 원인인지 본다. |
| `score_sec` | `3*A_delay_sec + N_delay_sec + T_recovery_sec` | 응급차 지연을 가장 크게 반영한다. |
| `signal_burden_penalty_sec` | `0.5*realized_extension_sec + 30*phase_switch_count` | 긴 green 연장보다 phase switch가 penalty에 크게 반영된다. |
| `bo_score_sec` | `score_sec + signal_burden_penalty_sec + failure_penalty_sec` | 최종 BO ranking은 `score_sec`가 아니라 이 값 기준이다. |
| `B2_vs_B0_travel_time_delta_sec` | B2 응급차 주행시간에서 B0 응급차 주행시간을 뺀다. | 음수면 B2가 B0보다 빠른 것이다. 최종 검증에서 중요하다. |

## 3. Bayesian Optimization 병렬 평가 구조

| 단계 | 무엇을 하는가 | 결과 CSV에서 확인할 것 |
| --- | --- | --- |
| 초기 후보 생성 | `D_det`, `alpha`, `G_ext` 조합을 initial theta로 만든다. | 초기 row들이 탐색 범위 안에 있는지 확인한다. |
| 병렬 평가 | 한 round에서 추천 theta 5개를 동시에 평가한다. 각 theta는 여러 repeat로 실행된다. | 같은 theta의 repeat별 `bo_score_sec` 편차와 `final_status`를 본다. |
| 학습 데이터 정리 | PASS/WARNING 중 유효한 row만 BO 관측치로 사용한다. | `bo_observations.csv`에 들어간 row와 `bo_excluded_observations.csv`에서 빠진 row를 비교한다. |
| 다음 후보 추천 | 누적 관측치의 평균 `bo_score_sec`를 기준으로 GP가 다음 theta 5개를 추천한다. | round가 진행될수록 best `bo_score_sec`가 낮아지는지 본다. |
| top 후보 산출 | 전체 관측치에서 평균 `bo_score_sec`가 낮은 theta를 top 후보로 고른다. | `score_sec`와 `signal_burden_penalty_sec`를 분리해서 본다. |

BO가 조정하는 값은 `D_det`, `alpha`, `G_ext`다. `T_change_sec=10`은 고정값이다.

GP는 이미 평가한 theta와 평균 `bo_score_sec` 관계를 근사하는 surrogate model이다. 아직 평가하지 않은 theta마다 “예상 점수”와 “불확실성”을 계산하고, Expected Improvement가 큰 후보를 다음 round 후보로 고른다. 한 round에서는 5개 theta를 batch로 뽑으며, 이미 평가한 theta와 batch 내부 중복은 제거한다.

| GP 추천 과정 | 설명 | CSV에서 확인할 점 |
| --- | --- | --- |
| 관측치 집계 | 같은 `D_det`, `alpha`, `G_ext`의 repeat 결과를 평균 `bo_score_sec`로 묶는다. | `bo_observations.csv`에서 theta별 유효 row와 score 분포를 본다. |
| surrogate 학습 | GP가 theta와 `bo_score_sec`의 관계를 근사한다. | round가 늘수록 추천 후보가 낮은 score 영역으로 이동하는지 본다. |
| 개선 가능성 계산 | 평가하지 않은 theta에 대해 Expected Improvement를 계산한다. | `bo_recommendations_round_XX.csv`의 후보가 기존 best 주변 또는 불확실한 영역을 탐색하는지 본다. |
| batch 추천 | 한 round에 5개 theta를 추천해 병렬 평가한다. | 같은 round 안에서 중복 theta가 없는지 본다. |
| 관측 업데이트 | round 평가 결과를 다시 GP 학습 데이터에 추가한다. | `bo_rounds.csv`에서 round별 best가 갱신되는지 본다. |

| 변수 | 의미 | CSV에서 봐야 할 점 |
| --- | --- | --- |
| `D_det` | 감지 거리 | 너무 작은 값은 제어가 늦고, 너무 큰 값은 신호 개입이 늘 수 있다. |
| `alpha` | 통과 후 green 유지 시간 | 낮으면 회복은 빠를 수 있지만 통과 직후 안정성을 봐야 한다. |
| `G_ext` | 통과 전 green 확보 상한 | 높으면 응급차에는 유리할 수 있지만 `realized_extension_sec`가 커질 수 있다. |
| `T_change_sec` | green 전환 요청 전 대기시간 | 최적화 대상이 아니며 10으로 고정됐는지만 확인한다. |

## 4. 최종 시뮬레이션에서 정해야 할 것

| 결정할 것 | 판단 기준 | 조언 |
| --- | --- | --- |
| 최종 theta 선택 | 평균 `bo_score_sec`가 낮고 `final_status`가 안정적인 후보 | `score_sec`만 낮은 후보보다 신호 부담까지 낮은 후보를 우선한다. |
| top 후보 재평가 여부 | repeat 편차가 크거나 WARNING이 섞인 후보 | 최종 채택 전 top3는 seed/repeat를 늘려 재평가하는 것이 좋다. |
| B0 대비 개선 인정 | `B2_vs_B0_travel_time_delta_sec`가 음수인 route가 충분한지 | 단일 route BO 결과만으로 최종 결론을 내리지 않는다. |
| 일반차 영향 허용 | `N_delay_sec`, `T_recovery_sec`가 과도하게 증가하지 않는지 | 응급차가 빨라져도 queue 회복이 크게 악화되면 재검토한다. |
| 신호 개입 허용 | `realized_extension_sec`, `phase_switch_count`가 설명 가능한 수준인지 | phase switch가 많은 theta는 운영 부담이 크다. |
| 정체 조건 인정 | `rolling_congestion_valid=True`인지 | 정체 조건이 깨진 row는 score가 좋아도 비교 신뢰도가 낮다. |
| 실패/경고 처리 | `final_status`, `warning_reason`, `failure_reason` | `FAIL`은 제외하고, `WARNING`은 사유가 경미한지 확인한다. |

## 5. 최종 검증 시뮬레이션, 05번 폴더 결과를 보는 법

05번 폴더 시뮬레이션은 BO에서 고른 theta가 서울역 단일 route에만 맞춘 결과가 아닌지 확인하는 독립 검증이다. 기본 관점은 “선택 theta가 18개 route에서도 B0보다 낫고, 안전하며, 신호 부담이 과하지 않은가”다.

| 결과 | 의미 | 확인할 점 |
| --- | --- | --- |
| `route_summary.csv` | route별 B00/B0/B2 비교 요약 | B2가 B0보다 빨라진 route 수와 악화 route를 본다. |
| `B2_vs_B0_travel_time_delta_sec` | route별 B2 개선량 | 음수면 개선, 양수면 악화다. |
| `score_components.csv` | route별 score 구성 | 어떤 route에서 `A_delay`, `N_delay`, `T_recovery`가 커졌는지 본다. |
| `experiment_summary.json` | 전체 검증 요약 | 실패 route, 개선 route 수, signal burden summary를 본다. |
| `signal_events.csv` | B2 신호 event | 특정 route에서 switch/extension/trim이 과도했는지 본다. |
| `task_status.json` | route/mode별 완료 상태 | FAIL/WARNING route를 분리한다. |

| 05번 검증 질문 | 기준 |
| --- | --- |
| 선택 theta가 여러 route에서 일관되게 개선되는가? | `route_summary.csv`에서 B2 delta 음수 route가 충분해야 한다. |
| 특정 route에서만 크게 악화되는가? | 악화 route의 `failure_reason`, `T_recovery_sec`, `signal_events.csv`를 확인한다. |
| 신호 부담이 route별로 편중되는가? | `realized_extension_sec`, `phase_switch_count`, signal event 수를 route별로 본다. |
| 응급차 안전 문제가 있는가? | emergency teleport, stop warning, lane connection warning이 있으면 후보 제외다. |

## 6. 결과 해석 조언

| 상황 | 해석 |
| --- | --- |
| `score_sec`는 낮지만 `bo_score_sec`가 높다 | 응급차/queue 지표는 좋아졌지만 신호 개입 부담이 커서 최적화 규칙상 좋은 후보가 아니다. |
| `A_delay_sec`는 낮지만 `N_delay_sec`가 높다 | 응급차는 빨라졌지만 일반차 부담이 커진 것이다. 운영 관점에서 재검토가 필요하다. |
| `T_recovery_sec`만 유독 높다 | 특정 TLS queue 회복이 병목이다. `queue_recovery_by_tls.csv`를 확인한다. |
| `phase_switch_count`가 높다 | green extension보다 적극적인 전환이 많았다는 뜻이다. penalty와 안전성을 같이 본다. |
| WARNING이 많다 | background teleport나 정체 유지 실패일 수 있다. 사유가 반복되면 해당 theta 비교 신뢰도를 낮춘다. |
| 05번 검증에서 일부 route만 악화 | 평균만 보지 말고 악화 route가 정책상 허용 가능한지 별도 판단한다. |
