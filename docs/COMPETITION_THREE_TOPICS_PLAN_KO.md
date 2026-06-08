# 대회 제출용 요약: B4 실험 3개 주제

작성 기준: 2026-06-08 현재 구현

## 한눈에 보기

| 주제 | 질문 | 제출 메시지 |
| --- | --- | --- |
| 1. BO vs Random Search vs CMA-ES | 같은 횟수로 실행했을 때 어떤 방법이 더 좋은 theta를 찾는가 | BO가 이전 결과를 보고 다음 후보를 고르는 최적화 방법임을 보여줍니다. |
| 2. 민감도 분석 | 응급차/일반차 지연 가중치가 바뀌면 결과가 어떻게 달라지는가 | 가중치 변화에 따른 trade-off를 보여줍니다. |
| 3. 검증용 시뮬레이션 | 잠근 theta가 실제 목적지 3곳에서도 안정적인가 | 10번 폴더에서 재최적화 없이 반복 검증합니다. |

## 공통 실험 기준

| 항목 | 값 |
| --- | --- |
| network | `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml` |
| demand | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml` |
| Stage1 | `data_prepared/compact_v9/b4_stage1_s1forced` |
| 결정변수 | `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau` |
| 기본 목적함수 | `(10/11) * D_E_sec + (1/11) * D_G_sec` |
| 비교 모드 | B004 자유류 기준, B04 무제어 baseline, B4 제어 적용 |

`D_E_sec`는 응급차 지연, `D_G_sec`은 일반차 지연입니다. score는 낮을수록 좋습니다.

## 1. 방법론 비교

비교 대상은 세 가지입니다.

| 방법 | 쉬운 설명 |
| --- | --- |
| Random Search | theta를 무작위로 뽑습니다. |
| CMA-ES | 진화전략으로 좋은 theta 방향을 찾아갑니다. |
| BO | 지금까지 실행한 결과를 보고 다음에 시험할 theta를 고릅니다. |

BO는 처음에는 몇 개를 무작위로 실행하고, 이후에는 score가 더 좋아질 가능성이 큰 후보를 선택합니다. 이 실험에서는 BO가 Random Search, CMA-ES보다 같은 실행 횟수 안에서 더 좋은 score를 찾는지 비교합니다.

제출용 비교 예산은 `n=15`, `m=50`입니다.

```bash
.venv/bin/python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id s1forced_fixed_budget_n15_m50 \
  --n 15 \
  --m 50 \
  --bo-initial 10 \
  --workers 6 \
  --essi-candidate-count 600
```

주요 산출물:

| 파일 | 설명 |
| --- | --- |
| `all_evaluations.csv` | 전체 theta 평가 결과 |
| `final_method_comparison_results.csv` | 제출용 방법론 비교 표 |
| `table1_best_so_far.csv` | 방법별 누적 최고 성능 |
| `table2_bo_surrogate.csv` | BO 추천 기록 |
| `figure1_best_so_far.png` | 방법별 성능 비교 그림 |
| `figure2_bo_surrogate.png` | BO 진행 그림 |

## 2. 민감도 분석

목적은 응급차 지연과 일반차 지연의 가중치가 바뀔 때 결과가 어떻게 이동하는지 확인하는 것입니다.

| 가중치 | 의미 |
| --- | --- |
| `1:1` | 응급차와 일반차를 같은 비중으로 봅니다. |
| `5:1` | 응급차를 더 중시합니다. |
| `10:1` | 현재 기본 정책 가중치입니다. |
| `15:1` | 응급차 우선 비중을 더 높입니다. |
| `20:1` | 가장 강한 응급차 우선 조건입니다. |

`10:1`은 기본값이지 정답이 아닙니다. 민감도 분석은 정책 선택을 돕는 비교표입니다.

주요 산출물:

| 파일 | 설명 |
| --- | --- |
| `table3_pareto.csv` | 가중치별 최적 theta와 지연값 |
| `final_sensitivity_results.csv` | 제출용 민감도 표 |
| `table4_sensitivity_spc.csv` | 가중치별 안정화 기록 |
| `figure3_pareto.png` | Pareto 비교 그림 |
| `figure4_sensitivity_spc.png` | 안정화 trace 그림 |

## 3. 검증용 시뮬레이션

10번 폴더의 이름은 `검증용 시뮬레이션`으로 설명합니다.

```text
10 Final Destination Validation/final_destination_validation.py
```

핵심은 재최적화가 아니라 검증입니다.

1. 9-1에서 고른 theta를 잠급니다.
2. 18개 후보 목적지를 screening합니다.
3. 최종 3개 목적지를 고릅니다.
4. 최종 3개 목적지에서 B004/B04/B4를 30회 반복 검증합니다.
5. 반복 결과의 평균과 안정성을 보고합니다.

제출용 실행:

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv" \
  --theta-method ALL \
  --workers 6 \
  --run-id validation_simulation_001
```

기존 이름을 유지할 때는 `--run-id final_destination_validation_001`을 써도 됩니다.

주요 산출물:

| 파일 | 설명 |
| --- | --- |
| `screening/candidate_selection.csv` | 18개 목적지 후보 screening 결과 |
| `final/candidate_selection.csv` | 최종 3개 목적지 요약 |
| `final/final_simulation_results.csv` | 제출용 최종 검증 표 |
| `final/selected_route_runs.csv` | B004/B04/B4 실행 row |
| `final/selected_mode_averages.csv` | mode별 평균 |
| `final/spc_repeat_stability.csv` | 반복 안정성 판단 |
| `final/final_destination_validation_report.md` | 최종 보고서 |

## 확인

문서와 runner 계약을 확인하는 최소 테스트:

```bash
.venv/bin/python -m pytest tests/test_b4_optimization_s1forced.py tests/test_final_destination_validation.py -q
```
