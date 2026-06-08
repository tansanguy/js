# B4 최적화와 검증 요약

작성 기준: 2026-06-08 현재 코드베이스

이 문서는 B4 실험의 세 흐름만 짧게 정리합니다.

1. BO vs Random Search vs CMA-ES 비교
2. 민감도 분석
3. 10번 폴더 `검증용 시뮬레이션`

## 공통 기준

| 항목 | 값 |
| --- | --- |
| net | `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml` |
| demand | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml` |
| Stage1 | `data_prepared/compact_v9/b4_stage1_s1forced` |
| active inputs | `configs/compact_v9_B04_B4_active_inputs.json` |
| 결정변수 | `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau` |
| 기본 score | `(10/11) * D_E_sec + (1/11) * D_G_sec` |

`D_E_sec`는 응급차 지연, `D_G_sec`은 일반차 지연입니다. score는 낮을수록 좋습니다.

## 1. BO와 다른 방법론 비교

실행 파일은 아래입니다.

```text
09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py
```

목적은 같은 실행 예산에서 어떤 방법이 더 좋은 theta를 찾는지 비교하는 것입니다.

| 방법 | 쉬운 설명 |
| --- | --- |
| Random Search | theta를 무작위로 뽑는 기준선입니다. |
| CMA-ES | 진화전략으로 좋은 theta 쪽을 찾아가는 비교 방법입니다. |
| BO | 지금까지 실행한 결과를 보고 다음에 시험할 theta를 고르는 최적화 방법입니다. |

BO는 처음에는 몇 개를 무작위로 실행하고, 이후에는 score가 더 좋아질 가능성이 큰 후보를 선택합니다. 이 실험에서는 BO가 Random Search, CMA-ES보다 같은 실행 횟수 안에서 더 좋은 score를 찾는지 비교합니다.

기본 제출용 비교 예산은 `n=15`, `m=50`입니다.

```bash
.venv/bin/python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id s1forced_fixed_budget_n15_m50 \
  --n 15 \
  --m 50 \
  --bo-initial 10 \
  --workers 6 \
  --essi-candidate-count 600
```

주요 산출물은 `09-1 B4 Optimization S1forced/outputs/{run_id}/`에 저장됩니다.

| 파일 | 의미 |
| --- | --- |
| `all_evaluations.csv` | 세 방법의 전체 theta 평가 결과 |
| `final_method_comparison_results.csv` | 제출/보고용 방법론 비교 표 |
| `table1_best_so_far.csv` | 방법별 누적 best-so-far |
| `table2_bo_surrogate.csv` | BO 관측값과 추천 기록 |
| `figure1_best_so_far.png` | 방법별 성능 변화 그림 |
| `figure2_bo_surrogate.png` | BO 추천 흐름 그림 |

## 2. 민감도 분석

민감도 분석은 응급차 지연과 일반차 지연의 가중치를 바꾸면 결과가 어떻게 달라지는지 보는 절차입니다. 가중치를 자동으로 정하는 절차가 아닙니다.

실행은 9-1 runner의 Pareto sweep에서 수행합니다.

```text
09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py::run_pareto()
```

확인하는 가중치는 아래 5개입니다.

| 가중치 | 의미 |
| --- | --- |
| `1:1` | 응급차와 일반차 지연을 같은 비중으로 봅니다. |
| `5:1` | 응급차 지연을 더 중시합니다. |
| `10:1` | 현재 기본 정책 가중치입니다. |
| `15:1` | 응급차 우선 비중을 더 높입니다. |
| `20:1` | sweep 안에서 가장 강한 응급차 우선 조건입니다. |

해석할 때 `10:1`은 기본값이지 정답이 아닙니다. 어떤 가중치를 쓸지는 정책 판단입니다.

주요 산출물은 아래입니다.

| 파일 | 의미 |
| --- | --- |
| `table3_pareto.csv` | 가중치별 최적 theta와 `D_E_sec`, `D_G_sec`, score |
| `final_sensitivity_results.csv` | 제출/보고용 민감도 표 |
| `table4_sensitivity_spc.csv` | 가중치별 BO 안정화 기록 |
| `figure3_pareto.png` | Pareto 후보 그림 |
| `figure4_sensitivity_spc.png` | 안정화 trace 그림 |

## 3. 검증용 시뮬레이션, 10번 폴더

10번 폴더는 최적화가 아니라 검증입니다.

```text
10 Final Destination Validation/final_destination_validation.py
```

실행 이름은 `검증용 시뮬레이션`으로 둡니다. 9-1에서 고른 theta를 잠그고, 실제 목적지 3곳에서 B004/B04/B4를 반복 비교합니다. 10번 안에서는 BO, CMA-ES, Random Search를 다시 실행하지 않습니다.

절차는 단순합니다.

1. 9-1 결과의 `all_evaluations.csv`에서 좋은 theta를 선택합니다.
2. 18개 후보 목적지를 screening합니다.
3. screening 결과로 최종 3개 목적지를 고릅니다.
4. 최종 3개 목적지에서 B004/B04/B4를 30회 반복 검증합니다.
5. 결과 CSV와 report로 개선폭과 안정성을 확인합니다.

제출용 실행 예시는 아래입니다.

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv" \
  --theta-method ALL \
  --workers 6 \
  --run-id validation_simulation_001
```

기존 run-id를 유지해야 하면 `final_destination_validation_001`을 사용해도 됩니다.

주요 산출물은 `results/metrics/compact_v9_final_destination_validation/{run_id}/`에 저장됩니다.

| 파일 | 의미 |
| --- | --- |
| `screening/candidate_selection.csv` | 18개 후보 screening 결과 |
| `final/candidate_selection.csv` | 최종 3개 목적지 요약 |
| `final/final_simulation_results.csv` | 제출/보고용 최종 검증 표 |
| `final/selected_route_runs.csv` | B004/B04/B4 전체 run row |
| `final/selected_mode_averages.csv` | route/mode별 평균 |
| `final/spc_repeat_stability.csv` | 30회 반복 안정성 판단 |
| `final/final_destination_validation_report.md` | 최종 보고서 |

## 문서 검증 명령

문서의 경로와 runner 계약을 확인하는 최소 테스트는 아래입니다.

```bash
.venv/bin/python -m pytest tests/test_b4_optimization_s1forced.py tests/test_final_destination_validation.py -q
```

실제 제출 결과는 full run 산출물의 CSV, PNG, report가 모두 생성됐는지 확인한 뒤 설명합니다.
