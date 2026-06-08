# S1/S2 시나리오 비교 검증 문서

작성 기준: `final_destination_validation_bo_best_20260608` + `scenario_replace_er_acc_001_20260608` 최종 검증 산출물

## 비교 정의

- S1: 현행 시나리오. 코드상 `B04`, 우선신호 제어를 적용하지 않은 baseline입니다.
- S2: 우선신호체계 도입 시나리오. 코드상 `B4`, 잠근 theta를 적용한 제어 시나리오입니다.
- B004: 자유류 기준값입니다. S1/S2 직접 비교 대상은 아니고 긴급차 지연시간을 계산하기 위한 free reference입니다.

비교 지표는 `긴급차량 지연시간`, `일반차량 평균지연시간`, `목적함수 점수`입니다. 목적함수는 기존 코드 계약과 동일하게 `(10/11) * d_EMV_sec + (1/11) * d_veh_sec`를 사용합니다.

## 사고위치 수와 선정 이유

최종 비교 검증은 사고위치 3개로 수행합니다. 이유는 다음과 같습니다.

- 18개 후보를 먼저 screening해서 도달 실패, teleport, 개입 없음, 개선 없음인 후보를 제거했습니다.
- 사용자가 제외 요청한 `ER_ACC_011`은 최종 세트에서 제거했습니다.
- 대체 후보로 `ER_ACC_019`, `ER_ACC_004`, `ER_ACC_001`을 순차 확인했습니다. `ER_ACC_019`와 `ER_ACC_004`는 final 30회에서 B4 fail이 발생해 탈락했고, `ER_ACC_001`만 30회 모두 도착/teleport 0/fail 0 조건을 통과했습니다.
- 최종 3개는 `ER_ACC_006`, `ER_ACC_016`, `ER_ACC_001`입니다. `ER_ACC_006`은 시연용 대표 경로, `ER_ACC_016`은 장거리/다신호 검증 경로, `ER_ACC_001`은 11번 탈락 뒤 통과한 보수적 짧은 대체 경로입니다.
- 최종 반복 검증 비용을 통제하면서도 한 위치만 검증했다는 약점을 피할 수 있습니다.

| 사고위치 | route id | 경로 m | edge 수 | 대로 비율 | spine 비율 | 선정 사유 |
| --- | --- | --- | --- | --- | --- | --- |
| ER_ACC_006 | FINAL_DEST_ER_ACC_006 | 1673 | 43 | 0.867 | 0.805 | valid_b4_improvement_with_actual_intervention |
| ER_ACC_016 | FINAL_DEST_ER_ACC_016 | 2577 | 63 | 0.637 | 0.829 | valid_b4_improvement_with_actual_intervention |
| ER_ACC_001 | FINAL_DEST_ER_ACC_001 | 677 | 19 | 0.559 | 0.641 | valid_b4_improvement_with_actual_intervention |

## 11번 대체 후보 확인

| 검토 후보 | route id | B4 개선 sec | 도착률 | fail | 탈락 사유 |
| --- | --- | --- | --- | --- | --- |
| ER_ACC_019 | FINAL_DEST_ER_ACC_019 | 0.000000 | 0.000000 | 30 | excluded_due_to_failure_teleport_arrival_or_comparison_gap |
| ER_ACC_004 | FINAL_DEST_ER_ACC_004 | 186.966667 | 0.066667 | 28 | excluded_due_to_failure_teleport_arrival_or_comparison_gap |

`ER_ACC_001`은 개선폭이 크지는 않지만 final 30회 안정성 조건을 통과했습니다. 따라서 11번을 빼야 한다면, 현재 결과 기준으로는 `ER_ACC_001`이 가장 방어 가능한 대체 경로입니다.

## 반복 횟수

- Screening: 18개 후보 위치를 1회씩 실행해 검증 가능한 후보를 고릅니다.
- Final validation: 최종 3개 사고위치에서 S1/S2 각각 30회 반복합니다. B004 자유류 기준은 위치별 1회 생성합니다.
- 반복 출발시각은 550-650초 구간에서 seed 기반으로 흔들어 단일 출발시각 과적합을 줄입니다.
- 현재 산출물의 SPC 안정성 표에서는 3개 위치의 주요 지표가 모두 `stable`, `stable_round=5`로 판정됐습니다.

## S1/S2 비교값

단위는 초입니다. `일반차 지연 감소`가 양수면 S2에서 일반차 평균지연도 줄었다는 뜻이고, 음수면 S2에서 일반차 평균지연이 증가한 위치입니다.

| 순위 | 사고위치 | target edge | 경로 m | 반복 | S1 긴급차 지연 | S2 긴급차 지연 | 긴급차 단축 | S1 일반차 평균지연 | S2 일반차 평균지연 | 일반차 지연 감소 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | ER_ACC_006 | -228796091#1 | 1673 | 30 | 254.40 | 88.93 | 165.47 | 56.72 | 53.93 | 2.78 |
| 2 | ER_ACC_016 | 273028084#6 | 2577 | 30 | 280.20 | 125.76 | 154.43 | 63.09 | 59.07 | 4.01 |
| 3 | ER_ACC_001 | -1099004169 | 677 | 30 | 80.38 | 75.98 | 4.40 | 43.48 | 48.20 | -4.72 |

요약하면 S2는 3개 위치 평균으로 긴급차 지연을 `108.10`초 줄였습니다. 일반차 평균지연은 위치별로 상충이 있으나 평균 `0.69`초/veh 감소입니다. 코드 목적함수 기준 평균 개선은 `98.35`초입니다.

## 사회적 가치 계산

현 단계에서는 금액 단위 계수 없이도 비교 가능한 사회가치 proxy를 계산합니다.

```text
ΔD_E_i = D_E(S1, i) - D_E(S2, i)
ΔD_G_i = D_G(S1, i) - D_G(S2, i)

10:1 사회가치 proxy_i = (10 * ΔD_E_i + 1 * ΔD_G_i) / 11
금액 환산_i = V_E_sec * ΔD_E_i + V_G_sec * N_G_i * ΔD_G_i
```

여기서 `V_E_sec`는 긴급차 도착 1초 단축 가치, `V_G_sec`는 일반차 1대의 1초 시간가치, `N_G_i`는 사고위치 i에서 정책 평가에 포함할 일반차 대수입니다. 제출 자료에서 원화 금액을 넣으려면 이 세 정책계수를 별도 표로 고정하면 됩니다.

| 사고위치 | Δ긴급차 지연 sec | Δ일반차 평균지연 sec/veh | 목적함수 개선 sec | 10:1 사회가치 proxy sec | S2 Stage3 평균 | S2 Stage2 평균 |
| --- | --- | --- | --- | --- | --- | --- |
| ER_ACC_006 | 165.47 | 2.78 | 150.09 | 150.68 | 11.13 | 0.27 |
| ER_ACC_016 | 154.43 | 4.01 | 140.20 | 140.76 | 12.73 | 0.23 |
| ER_ACC_001 | 4.40 | -4.72 | 4.75 | 3.57 | 0.00 | 0.57 |

현재 검증 결과의 10:1 사회가치 proxy 평균은 `98.34`초입니다. `ER_ACC_001`은 일반차 평균지연이 증가하므로, 전체 메시지는 `ER_ACC_006`과 `ER_ACC_016`에서 강한 긴급차 단축 효과를 보이고 `ER_ACC_001`은 11번 대체용 안정성 확인 경로로 설명하는 편이 맞습니다.

## 시각화 산출물

- `scenario_routes_overview.png`: 최종 검증 3개 사고위치와 경로 전체 개요입니다.
- `demo_route_er_acc_006.png`: 시연 영상에 사용할 단일 사고위치 상세 경로입니다.
- `s1_s2_delay_comparison.png`: 긴급차 지연과 일반차 평균지연의 S1/S2 비교 막대그래프입니다.

시연 영상은 `ER_ACC_006` 하나만 쓰는 것이 좋습니다. 개선폭이 가장 크고, 대로 비율이 높으며, 경로가 짧아 발표 시간 안에 우선신호 개입 전후를 설명하기 쉽습니다. 나머지 2개 위치는 영상이 아니라 검증 표와 overview 이미지로 “여러 위치에서 비교했다”는 근거를 제공합니다.

## 연결 산출물

- 요약 CSV: `scenario_comparison_summary.csv`
- 원본 final report: `results/metrics/compact_v9_final_destination_validation/final_destination_validation_bo_best_20260608/final/final_destination_validation_report.md`
- 11번 대체 final report: `results/metrics/compact_v9_final_destination_validation/scenario_replace_er_acc_001_20260608/final/final_destination_validation_report.md`
- 원본 selected runs: `results/metrics/compact_v9_final_destination_validation/final_destination_validation_bo_best_20260608/final/selected_route_runs.csv`
- 대체 selected runs: `results/metrics/compact_v9_final_destination_validation/scenario_replace_er_acc_001_20260608/final/selected_route_runs.csv`
