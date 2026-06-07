# 대회 제출 계획서: B4 최적화 3개 주제

작성 기준: 2026-06-07 현재 구현

## 제출 주제 요약

| 주제 | 핵심 질문 | 구현 산출물 | 발표 메시지 |
| --- | --- | --- | --- |
| 1. BO vs 다른 방법론 | 같은 예산에서 어떤 최적화 방법이 더 좋은 B4 theta를 찾는가 | Random Search, CMA-ES, `GP+ESSI` BO fixed-budget 비교 | BO는 단순 label이 아니라 ESSI를 acquisition으로 쓰는 실제 탐색 알고리즘 |
| 2. 민감도 분석 | 응급차 지연과 일반차 지연의 가중치가 바뀌면 해가 어떻게 이동하는가 | ESSI-aware Pareto BO sweep + SPC trace | 정책 가중치 변화에 따른 trade-off와 안정화 여부를 같이 제시 |
| 3. 최종 시뮬레이션 | 잠근 theta가 실제 목적지 3곳에서 반복적으로 안정적인가 | 18개 목적지 screening, 최종 3개 30-repeat 검증, SPC 안정성 표, 경로 HTML | 최종 단계는 재최적화가 아니라 locked-theta 검증 |

## 공통 실험 기준

| 항목 | 값 |
| --- | --- |
| network | `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml` |
| demand | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml` |
| Stage1 | `data_prepared/compact_v9/b4_stage1_s1forced` |
| 결정변수 | `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau` |
| 기본 목적함수 | `(10/11) * delay_A + (1/11) * delay_N` |
| B004/B04/B4 의미 | B004 자유류 기준, B04 무제어 baseline, B4 제어 적용 |

`delay_A`는 응급차 지연, `delay_N`은 일반차 지연입니다. 모든 score는 낮을수록 좋습니다.

## 1. 방법론 비교: Random Search vs CMA-ES vs BO

### 목표

동일한 `n` seed와 `m` round budget에서 세 방법이 실제로 theta를 탐색하게 하고, 누적 best-so-far와 최종 score를 비교합니다.

### 비교 방법

| 방법 | 역할 | 구현 방식 |
| --- | --- | --- |
| Random Search | 하한선 baseline | theta 범위에서 seed별 `m`개 후보를 무작위 평가 |
| CMA-ES | 진화전략 기반 비교군 | Python `cma` 패키지의 `CMAEvolutionStrategy`를 `[0,1]^5` 정규화 공간에서 실행 |
| BO | 제안 방법 | 초기 random observation 후 `GP + ESSI` acquisition으로 다음 theta 선택 |

### BO는 어떻게 동작하는가

1. `bo_initial` round까지는 random initial design으로 관측치를 확보합니다.
2. 이후 관측된 theta와 score를 사용해 GP surrogate를 학습합니다.
3. candidate pool에 대해 GP 예측 평균과 불확실성을 계산합니다.
4. GP 기반 개선 가능성을 각 spatial subspace에 투영해 `ESSI_i`를 계산합니다.
5. `essi_acquisition = max_i ESSI_i`가 가장 큰 theta를 다음 round에 평가합니다.

### GP 설명

GP, Gaussian Process는 목적함수 `f(theta)`를 직접 모르는 상태에서 관측된 theta-score 관계를 확률적으로 근사하는 surrogate model입니다.

BO에서 GP가 주는 값은 두 가지입니다.

| 값 | 의미 | BO에서의 역할 |
| --- | --- | --- |
| 예측 평균 `mu(theta)` | 해당 theta의 예상 score | 낮을수록 exploit 후보 |
| 예측 표준편차 `sigma(theta)` | 아직 모르는 정도 | 클수록 explore 후보 |

즉 GP는 “좋아 보이는 지점”과 “불확실해서 확인할 가치가 있는 지점”을 동시에 표현합니다. B4 시뮬레이션은 한 번 평가하는 비용이 크기 때문에, 모든 theta를 훑지 않고 GP surrogate로 다음 후보를 고릅니다.

### GP 개선 가능성 설명

ESSI는 GP가 예측한 개선 가능성을 spatial subspace별로 나눠 측정합니다. minimization 문제에서는 현재 best를 `f_best`라고 할 때 기본 개선량을 아래처럼 봅니다.

```text
improvement(theta) = max(f_best - f(theta), 0)
```

이 값은 ESSI 계산의 내부 재료일 뿐, 최종 BO acquisition으로 직접 쓰지 않습니다. 최종 선택 기준은 항상 subspace별 ESSI입니다.

### ESSI 설명

ESSI는 `Expected Spatial Search Improvement`입니다. 이 프로젝트에서는 BO가 단순히 전체 score 개선 가능성만 보고 움직이지 않고, Stage1에서 공간적으로 중요한 제어 구간의 개선 가능성을 subspace별로 측정하는 acquisition입니다.

ESSI는 다음 이유로 필요합니다.

- B4 제어는 route 전체에 균일하게 작동하지 않고 병목, 합류, Case B 후보, 제어 가능한 movement가 있는 구간에서 효과가 큽니다.
- 같은 전체 개선 가능성이라도 공간적으로 중요한 subspace를 활성화하는 theta가 더 의미 있을 수 있습니다.
- 따라서 ESSI는 BO가 “개선 가능성”과 “공간 제어 중요도”를 같이 보게 만듭니다.

구현 절차는 다음입니다.

1. Stage1 route movement를 route-order 기준 6개 spatial subspace로 나눕니다.
2. 각 subspace에 대해 weight를 계산합니다.
3. weight는 controllable movement density, Case B candidate count, bottleneck/control candidate presence를 반영합니다.
4. weight는 0-1 범위로 정규화합니다.
5. 각 candidate theta가 어떤 제어 성격을 강화하는지 `spatial_activation_score`로 계산합니다.
6. 각 subspace별 `ESSI_i`를 계산하고 그 최댓값을 최종 acquisition으로 씁니다.

최종 acquisition은 고정식입니다.

```text
ESSI_i(theta) = GP_improvement(theta) * spatial_subspace_activation_i(theta)
essi_acquisition(theta) = max_i ESSI_i(theta)
```

따라서 이 파이프라인의 BO는 ESSI-only acquisition으로 다음 theta를 선택합니다.

### 방법론 비교 산출물

| 파일 | 설명 |
| --- | --- |
| `all_evaluations.csv` | 세 방법의 모든 theta 평가 결과 |
| `final_method_comparison_results.csv` | 제출용 clean CSV. input, 목적함수 output 2개, weight, score, 실측값, Stage2/Stage3 on 횟수만 포함 |
| `table1_best_so_far.csv` | method/seed별 누적 best-so-far |
| `table2_bo_surrogate.csv` | BO 관측값, GP surrogate, ESSI acquisition |
| `bo_spatial_subspaces.json` | ESSI용 6개 spatial subspace와 weight |
| `figure1_best_so_far.png` | 방법별 best-so-far 평균 및 95% CI |
| `figure2_bo_surrogate.png` | BO surrogate trace |
| `experiment_summary.json` | `bo_algorithm="GP+ESSI"` 기록 |

3개 방법론 비교에는 SPC stop/status를 넣지 않습니다. SPC는 민감도 분석과 최종 반복 안정성 판단에만 사용합니다.

## 2. 민감도 분석: ESSI-aware Pareto BO + SPC

### 목표

응급차 지연과 일반차 지연의 상대 가중치를 바꿨을 때 최적 theta와 trade-off가 어떻게 달라지는지 확인합니다.

### Sweep 조건

| weight ratio | 의미 |
| --- | --- |
| `1:1` | 응급차와 일반차 지연을 동일 비중으로 봄 |
| `5:1` | 응급차 지연을 더 중시 |
| `10:1` | 기본 정책 가중치 |
| `15:1` | 응급차 우선 강도 증가 |
| `20:1` | sweep 내 가장 강한 응급차 우선 조건 |

각 가중치에서도 BO는 동일하게 `GP+ESSI`를 사용합니다. 즉 민감도 분석은 단순 grid sweep이 아니라 가중치별 실제 ESSI-only BO 탐색입니다.

### SPC 설명

SPC, Statistical Process Control은 값이 충분히 안정화됐는지 또는 아직 변화 중인지 판단하는 통계적 관리 절차입니다.

이 프로젝트에서 SPC는 두 곳에만 사용합니다.

| 적용 위치 | SPC가 보는 값 | 목적 |
| --- | --- | --- |
| Pareto 민감도 분석 | round별 `essi_log_max`와 EWMA | 가중치별 ESSI trace가 안정화됐는지 판단 |
| 최종 30-repeat 검증 | route/metric별 반복 결과 | locked theta 결과가 반복 실행에서 안정적인지 판단 |

SPC는 3개 방법론 비교 BO에는 들어가지 않습니다.

### SPC 상태 정의

| 상태 | 의미 |
| --- | --- |
| `warmup` | 아직 판단할 round가 부족함 |
| `stable` | 최근 trace가 관리한계 안에 있고 안정화된 상태 |
| `active` | trace가 아직 변동 중이거나 관리한계 밖으로 나간 상태 |
| `insufficient` | 최종 repeat 수가 판단하기에 부족함 |

민감도 분석에서는 ESSI trace를 그대로 보지 않고 log와 EWMA를 함께 봅니다.

```text
essi_log_max = log(essi_max + epsilon)
EWMA_t = alpha * essi_log_max_t + (1 - alpha) * EWMA_{t-1}
```

EWMA는 최근 변화에 더 민감한 안정화 지표입니다. 관리한계는 최근 window의 평균과 표준편차로 계산합니다.

```text
LCL = center - 3 * sigma
UCL = center + 3 * sigma
```

EWMA가 관리한계 안에서 안정화되면 `stable`, 관리한계 밖이거나 변동이 계속되면 `active`로 봅니다.

### 민감도 산출물

| 파일 | 설명 |
| --- | --- |
| `table3_pareto.csv` | 가중치별 최적 theta, `delay_A`, `delay_N`, score, SPC 상태, knee 여부 |
| `final_sensitivity_results.csv` | 제출용 clean CSV. 각 가중치별 best row만 방법론 비교와 같은 clean 컬럼으로 기록 |
| `table4_sensitivity_spc.csv` | 가중치별 BO round의 ESSI/SPC trace |
| `figure3_pareto.png` | Pareto 후보와 knee point |
| `figure4_sensitivity_spc.png` | 가중치별 ESSI log-max EWMA trace |

해석에서 중요한 점은 knee point나 `10:1`을 정답이라고 쓰지 않는 것입니다. 이 분석은 정책 선택을 돕는 trade-off 표입니다.

## 3. 최종 시뮬레이션: locked theta 반복 검증

### 목표

9-1에서 선택한 theta를 잠그고 실제 목적지 3곳에서 B004/B04/B4를 비교합니다. 이 단계에서는 BO, CMA-ES, Random Search를 다시 실행하지 않습니다.

### 절차

1. 9-1 full fixed-budget 결과의 `all_evaluations.csv`에서 `final_status=PASS`인 theta 중 score 최저 theta를 선택합니다.
2. `05_theta_check_simulation/routes/b0_valid_18_routes.csv`의 18개 target edge를 읽습니다.
3. 최신 S1-forced net에서 소방서 출발 edge `420331801#1`부터 각 target까지 shortest route를 다시 만듭니다.
4. screening phase에서 각 후보를 B004 1회, B04 1회, B4 1회로 평가합니다.
5. EV 도착, teleport 없음, B4 개선, 실제 개입량 조건을 만족하는 후보를 남깁니다.
6. 개선폭, B04 지연, 개입량, mainroad/spine 대표성 기준으로 최종 3개 목적지를 고릅니다.
7. final phase에서 목적지 3개를 각각 `1 B004 + 30 B04 + 30 B4` 구조로 반복 검증합니다.
8. final repeat 결과에 SPC를 적용해 route/metric별 안정성을 판단합니다.

10번의 3개 목적지 선택에는 ESSI를 쓰지 않습니다. ESSI는 BO acquisition 구성요소이고, 최종 목적지 선택은 screening 결과 기반입니다.

### 최종 반복 SPC

final phase에서 SPC를 적용하는 metric은 다음입니다.

| metric | 의미 |
| --- | --- |
| `B4_vs_B04_improvement_sec` | B04 대비 B4 응급차 통행시간 개선폭 |
| `B4_d_EMV_sec` | B4 응급차 지연 |
| `B4_general_mean_travel_time_sec` | B4 일반차 평균 통행시간 |
| `B4_intervention_count` | Stage2 hold + Stage3 preemption 개입량 |

반복 수가 부족하면 `insufficient`, 관리한계 안에서 안정적이면 `stable`, 아직 변동 중이면 `active`로 기록합니다.

### 최종 시뮬레이션 경로 HTML

경로 검토용 HTML은 아래 파일입니다.

```text
results/html/compact_v9_final_destination_route_plan.html
```

이 HTML은 현재 dry-run 기준 18개 후보 경로를 보여줍니다.

- 빨간 경로: dry-run route priority 상위 3개
- 파란 경로: 그 외 실행 가능한 후보
- 제외 후보: `ER_ACC_018`, 최신 S1-forced net에 target edge가 없어 precheck 제외

주의: 이 HTML의 빨간 경로는 최종 결과가 아니라 screening 전 경로 계획입니다. 실제 최종 3개 목적지는 screening 실행 후 `final/selected_destinations.json`과 report로 확정합니다.

### 최종 산출물

| 파일 | 설명 |
| --- | --- |
| `screening/candidate_selection.csv` | 18개 후보 screening 결과와 선정/제외 사유 |
| `final/candidate_selection.csv` | 최종 3개 목적지의 30-repeat 요약 |
| `final/final_simulation_results.csv` | 제출용 clean CSV. B4 repeat row만 input, 목적함수 output 2개, weight, score, 실측값, Stage2/Stage3 on 횟수로 기록 |
| `final/selected_route_runs.csv` | 최종 목적지의 B004/B04/B4 run row |
| `final/selected_mode_averages.csv` | route/mode별 평균 |
| `final/spc_repeat_stability.csv` | route/metric별 SPC 안정성 |
| `final/final_destination_validation_report.md` | 최종 보고서 |
| `results/html/compact_v9_final_destination_route_plan.html` | 최종 검증 후보 경로 HTML |

제출용 clean CSV 3개는 모두 score 바로 왼쪽에 weight를 둡니다.

```text
... output_delay_A_sec, output_delay_N_sec, weight_A, weight_N, weight_ratio, score, ...
```

## 실행 명령

### 9-1 방법론 비교

```bash
.venv/bin/python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id s1forced_fixed_budget_n15_m50 \
  --n 15 \
  --m 50 \
  --bo-initial 10 \
  --workers 6 \
  --essi-candidate-count 600
```

BO를 먼저 별도 실행한 뒤 나머지 두 방법론을 이어붙일 수 있습니다.

```bash
.venv/bin/python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id s1forced_staged_bo_first \
  --methods bo \
  --n 15 \
  --m 50 \
  --bo-initial 10 \
  --essi-candidate-count 600 \
  --skip-pareto \
  --skip-noise-check

.venv/bin/python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id s1forced_staged_bo_first \
  --methods random cma \
  --append-existing \
  --n 15 \
  --m 50 \
  --bo-initial 10 \
  --essi-candidate-count 600 \
  --skip-pareto \
  --skip-noise-check
```

### 10번 경로 계획 dry-run

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase screening \
  --dry-run \
  --run-id final_route_plan_dry_run \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv"
```

### 경로 HTML 생성

```bash
.venv/bin/python "10 Final Destination Validation/make_final_route_plan_html.py"
```

### 10번 제출용 final validation

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv" \
  --theta-method ALL \
  --workers 6 \
  --run-id final_destination_validation_001
```

## 검증 체크리스트

- 3개 방법론 비교에서 BO row에 `essi_acquisition`이 기록되는가
- 3개 방법론 비교 산출물에 SPC stop/status가 빠져 있는가
- `bo_spatial_subspaces.json`의 6개 weight가 0-1 범위인가
- `experiment_summary.json`에 `bo_algorithm="GP+ESSI"`가 기록되는가
- Pareto 산출물에 ESSI/SPC trace가 기록되는가
- final phase에서 `spc_repeat_stability.csv`가 생성되는가
- 경로 HTML이 18개 후보와 precheck 제외 후보를 명확히 보여주는가
