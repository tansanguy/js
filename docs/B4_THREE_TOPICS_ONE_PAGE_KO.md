# B4 실험 3개 주제 한 페이지 요약

작성 기준: 2026-06-08

## 1. BO vs Random Search vs CMA-ES

목적은 같은 실행 횟수에서 어떤 방법이 더 좋은 B4 theta를 찾는지 비교하는 것입니다.

| 방법 | 의미 |
| --- | --- |
| Random Search | theta를 무작위로 뽑는 기준선 |
| CMA-ES | 진화전략으로 좋은 theta 방향을 찾아가는 비교 방법 |
| BO | 지금까지 실행한 결과를 보고 다음에 시험할 theta를 고르는 방법 |

BO는 처음에는 몇 개를 무작위로 실행하고, 이후에는 score가 더 좋아질 가능성이 큰 후보를 선택합니다. 이 실험에서는 BO가 Random Search, CMA-ES보다 같은 실행 횟수 안에서 더 좋은 score를 찾는지 확인합니다.

제출용 비교 예산은 `n=15`, `m=50`입니다.

## 2. 검증용 시뮬레이션, 10번 폴더

10번 폴더는 최적화가 아니라 검증입니다. 9-1에서 고른 theta를 잠그고, 실제 목적지에서 B004/B04/B4를 비교합니다.

| 단계 | 내용 |
| --- | --- |
| theta 선택 | 9-1 결과의 `all_evaluations.csv`에서 좋은 theta를 선택 |
| screening | 18개 후보 목적지를 B004/B04/B4로 1회씩 확인 |
| 최종 목적지 | screening 결과로 3개 목적지 선정 |
| final 검증 | 최종 3개 목적지에서 30회 반복 검증 |

실행 이름은 `검증용 시뮬레이션`으로 설명합니다. 10번 실행 중에는 BO, CMA-ES, Random Search를 다시 돌리지 않습니다.

대표 실행:

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv" \
  --theta-method ALL \
  --workers 6 \
  --run-id validation_simulation_001
```

## 3. 민감도 분석

목적은 응급차 지연과 일반차 지연의 가중치가 바뀌면 결과가 어떻게 달라지는지 확인하는 것입니다.

| 가중치 | 의미 |
| --- | --- |
| `1:1` | 응급차와 일반차 지연을 같은 비중으로 봄 |
| `5:1` | 응급차 지연을 더 중시 |
| `10:1` | 현재 기본 정책 가중치 |
| `15:1` | 응급차 우선 비중 증가 |
| `20:1` | 가장 강한 응급차 우선 조건 |

`10:1`은 기본값이지 정답이 아닙니다. 민감도 분석은 정책 선택을 돕는 trade-off 표입니다.

주요 산출물은 `table3_pareto.csv`, `final_sensitivity_results.csv`, `table4_sensitivity_spc.csv`, `figure3_pareto.png`, `figure4_sensitivity_spc.png`입니다.

## 최종 산출물 위치

| 구분 | 위치 |
| --- | --- |
| 방법론 비교 | `09-1 B4 Optimization S1forced/outputs/{run_id}/` |
| 검증용 시뮬레이션 | `results/metrics/compact_v9_final_destination_validation/{run_id}/` |
| 민감도 분석 | `09-1 B4 Optimization S1forced/outputs/{run_id}/` |

## 확인 명령

```bash
.venv/bin/python -m pytest tests/test_b4_optimization_s1forced.py tests/test_final_destination_validation.py -q
```
