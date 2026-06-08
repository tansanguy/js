# B4 최적화와 최종 시뮬레이션 문서

작성 기준: 2026-06-07 현재 코드베이스

이 문서는 현재 구현되어 있는 세 흐름을 한 번에 설명합니다.

1. BO와 다른 방법론 비교
2. 민감도 분석
3. 최종 시뮬레이션, 즉 `10 Final Destination Validation`

## 공통 실행 기준

최신 9번 계열 canonical profile은 `B04_B4_S1_FORCED_OPTIMIZATION`입니다.

| 항목 | 값 |
| --- | --- |
| net | `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml` |
| demand | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml` |
| Stage1 | `data_prepared/compact_v9/b4_stage1_s1forced` |
| Stage1 measurement source | `B04_ad_stage23_trigger` |
| active manifest | `configs/compact_v9_B04_B4_active_inputs.json` |
| 결정변수 | `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau` |
| 기본 score | `(10/11) * D_E + (1/11) * D_G` |

`D_E`는 응급차 자유류 대비 지연, `D_G`는 `V_G` 영향권 일반차 대당 평균 지연입니다. 산출물의 `D_E_sec`는 `D_E`, `D_G_sec`은 `D_G`에 대응합니다. `V_G`는 본선 route edge와 본선 교차로 TLS의 SUMO `.net.xml` incoming edge를 합쳐 자동 구성합니다. score는 낮을수록 좋습니다. `10:1` 가중치는 내부에서 `10/11`, `1/11`로 정규화됩니다.

## 전체 구조

| 구분 | 구현 위치 | 목적 | 재최적화 여부 |
| --- | --- | --- | --- |
| BO vs 다른 방법론 | `09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py` | Random Search, CMA-ES, BO를 같은 fixed-budget으로 비교 | 예 |
| 민감도 분석 | `09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py::run_pareto()` | ESSI-aware Pareto BO sweep으로 응급차/일반차 지연 trade-off 확인 | 예, 가중치별 BO |
| 최종 시뮬레이션 | `10 Final Destination Validation/final_destination_validation.py` | 잠근 theta를 실제 목적지 3곳에 적용해 B004/B04/B4 반복 검증 | 아니오 |

## 1. BO와 다른 방법론 비교

정본 runner는 아래 파일입니다.

```text
09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py
```

목적은 동일한 net, demand, Stage1, 결정변수 범위, 목적함수, 평가 budget 아래에서 세 방법을 비교하는 것입니다.

| 방법 | 현재 구현 |
| --- | --- |
| Random Search | theta search space에서 seed별로 `m`개 후보를 무작위 평가합니다. |
| CMA-ES | Python `cma` 패키지의 `CMAEvolutionStrategy`를 사용합니다. `[0, 1]^5` 정규화 공간에서 후보를 만들고 실제 theta 범위로 되돌려 평가합니다. |
| BO | 초기 `bo_initial` round는 random observation으로 채우고, 이후 Gaussian Process surrogate와 ESSI acquisition으로 다음 theta를 고릅니다. |

BO는 단순 설명용 label이 아니라 실제 해를 찾는 최적화 알고리즘입니다. 이 구현에서 BO 알고리즘 이름은 `GP+ESSI`입니다.

### ESSI 반영 방식

ESSI는 `Expected Spatial Search Improvement`입니다. BO가 Stage1 route movement 중 공간적으로 중요한 구간의 개선 가능성을 subspace별로 측정하도록 만든 acquisition입니다.

구현은 Stage1 route movement를 route-order 기준 6개 spatial subspace로 나누고, 각 subspace weight를 다음 값으로 0-1 정규화합니다.

- controllable movement density
- Case B candidate count
- bottleneck/control candidate presence

BO post-initial round에서는 candidate pool의 objective score를 GP로 예측하고, 후보별 개선 가능성을 6개 spatial subspace에 투영해 아래 acquisition을 만듭니다.

```text
ESSI_i(theta) = GP_improvement(theta) * spatial_subspace_activation_i(theta)
essi_acquisition(theta) = max_i ESSI_i(theta)
```

따라서 최종 BO 선택 기준은 ESSI-only acquisition입니다.

GP/ESSI 후보 생성에 실패하면 random fallback으로 조용히 넘어가지 않고 `gp_essi_unavailable` 오류로 실패를 드러냅니다.

### 평가 정책

- 기본 fixed-budget은 `n=15`, `m=50`입니다.
- `n`은 방법별 seed 수이고, `m`은 seed 하나당 theta 평가 round 수입니다.
- full run 기준 총 본 비교 평가는 `3 methods * 15 seeds * 50 rounds = 2250`개입니다.
- 실패 run은 penalty를 받아 score가 커집니다.
- BO row에는 ESSI acquisition 필드를 기록합니다.
- SPC stop/status 필드는 3개 방법론 비교 산출물에 넣지 않습니다.
- fixed-budget 비교 본체는 조기 종료하지 않고 `m` round를 채웁니다.
- Pareto sweep과 noise check는 본 비교 뒤에 별도 artifact로 생성됩니다.

### 실행

Mock smoke:

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --mock-eval \
  --run-id s1forced_mock_smoke \
  --n 2 \
  --m 5 \
  --bo-initial 2 \
  --workers 6 \
  --ei-candidate-count 20
```

Real smoke:

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id s1forced_real_smoke \
  --n 1 \
  --m 4 \
  --bo-initial 2 \
  --workers 6 \
  --ei-candidate-count 50 \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml" \
  --stage1-dir "data_prepared/compact_v9/b4_stage1_s1forced" \
  --hard-max-sim-time 4000
```

제출용 fixed-budget run:

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id s1forced_fixed_budget_n15_m50 \
  --n 15 \
  --m 50 \
  --bo-initial 10 \
  --workers 6 \
  --essi-candidate-count 600 \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml" \
  --stage1-dir "data_prepared/compact_v9/b4_stage1_s1forced" \
  --hard-max-sim-time 4000
```

### 산출물

산출물은 `09-1 B4 Optimization S1forced/outputs/{run_id}/`에 저장됩니다.

| 파일 | 의미 |
| --- | --- |
| `all_evaluations.csv` | 전체 theta 평가 결과 long-form 표 |
| `final_method_comparison_results.csv` | 제출/보고용 방법론 비교 clean CSV |
| `table1_best_so_far.csv` | 방법별/seed별 누적 best-so-far 표 |
| `table2_bo_surrogate.csv` | BO 관측값, surrogate mean/CI, ESSI acquisition 표 |
| `bo_spatial_subspaces.json` | ESSI용 6개 spatial subspace 정의와 weight |
| `figure1_best_so_far.png` | 방법별 best-so-far 평균과 95% CI |
| `figure2_bo_surrogate.png` | BO surrogate trace |
| `experiment_summary.json` | 입력, 예산, seed, 산출물 manifest |

`table2_bo_surrogate.csv`와 BO row의 `all_evaluations.csv`에는 `essi_acquisition`, `essi_1`부터 `essi_6`, `essi_max`, `essi_mean`, `essi_log_max`, `dominant_essi_subspace`, `spatial_activation_score`가 기록됩니다. Random Search/CMA-ES row의 ESSI 필드는 비어 있습니다.

full `n=15`, `m=50` run이 실제로 완료됐는지는 `all_evaluations.csv` row 수, `figure1_best_so_far.png`, `figure2_bo_surrogate.png`, `experiment_summary.json`을 확인한 뒤에만 말할 수 있습니다.

## 2. 민감도 분석

현재 재실행 가능한 민감도 분석의 정본은 9-1 fixed-budget runner에 포함된 Pareto 가중치 sweep입니다.

```text
09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py::run_pareto()
```

이 분석은 목적함수 가중치를 바꿨을 때 응급차 지연과 일반차 지연이 어떻게 맞교환되는지 보여줍니다. 가중치를 자동으로 정하는 절차가 아닙니다.

각 가중치 탐색도 BO를 사용하므로 3개 방법론 비교의 BO와 같은 ESSI-adjusted acquisition을 씁니다. 차이는 민감도 분석에는 ESSI trace 안정화 판단을 위한 SPC가 추가된다는 점입니다.

### Sweep 조건

| 가중치(w1:w2) | 의미 |
| --- | --- |
| `1:1` | 응급차 지연과 일반차 지연을 같은 비율로 봅니다. |
| `5:1` | 응급차 지연을 더 강하게 봅니다. |
| `10:1` | 현재 기본 정책 가중치입니다. |
| `15:1` | 응급차 우선 비중을 더 높입니다. |
| `20:1` | sweep 내 가장 강한 응급차 우선 조건입니다. |

각 가중치에서는 net, demand, Stage1, 사고 위치, 출동 조건을 고정하고 ESSI-aware BO 탐색 1회를 수행합니다. 기본 Pareto sweep은 ESSI/SPC trace가 안정화되면 조기 중단할 수 있습니다.

### 산출물과 해석

| 파일 | 의미 |
| --- | --- |
| `table3_pareto.csv` | 가중치별 최적 theta, `D_E_sec`, `D_G_sec`, score, SPC 중단 여부, knee 표시 |
| `final_sensitivity_results.csv` | 제출/보고용 민감도 clean CSV |
| `table4_sensitivity_spc.csv` | 가중치별 BO round의 ESSI/SPC trace |
| `figure3_pareto.png` | 가중치별 Pareto 후보와 knee point 보조 표시 |
| `figure4_sensitivity_spc.png` | 가중치별 ESSI log-max EWMA/SPC trace |

해석 원칙:

- `D_E_sec`가 줄면 `D_G_sec`이 늘 수 있고, 반대도 가능합니다.
- 주황색 knee point는 설명 보조 표시입니다.
- knee point나 `10:1`을 정답이라고 쓰지 않습니다.
- 최종 가중치 선택은 정책 결정 영역입니다.
- 실제 30회 반복을 수행하지 않았다면 30회 반복 결과라고 쓰지 않습니다.

### 기존 diagnostic sensitivity 산출물

`09 Compact Corridor Baseline/tdata_signal/*sensitivity*` 아래에는 fixed-param 또는 OFAT sensitivity 보고서가 남아 있습니다. 예시는 다음과 같습니다.

| 산출물 | 성격 |
| --- | --- |
| `09 Compact Corridor Baseline/tdata_signal/active15_fixed_param_sensitivity/fixed_param_sensitivity_report.md` | active15 조건에서 tau 계열 fixed-param 민감도 확인 |
| `09 Compact Corridor Baseline/tdata_signal/u130_ofat_sensitivity/ofat_sensitivity_report.md` | u130 조건에서 단일 변수 변경 OFAT 확인 |
| `09 Compact Corridor Baseline/tdata_signal/u130_toegye15_fixed_param_sensitivity/fixed_param_sensitivity_report.md` | u130/toegye15 조건에서 구조 파라미터 후보를 확인한 diagnostic 결과 |

다만 현재 작업트리에서 기존 재실행 스크립트 `run_b4_fixed_param_sensitivity.py`, `run_b4_theta_ofat_sensitivity.py`는 삭제 상태입니다. 따라서 제출용 현재 정본 민감도 분석은 9-1 runner의 `table3_pareto.csv`, `table4_sensitivity_spc.csv`, `figure3_pareto.png`, `figure4_sensitivity_spc.png`로 설명합니다. 과거 fixed-param/OFAT 결과는 알고리즘 튜닝 과정의 diagnostic artifact로만 다룹니다.

## 3. 최종 시뮬레이션, 10번 폴더

정본 runner는 아래 파일입니다.

```text
10 Final Destination Validation/final_destination_validation.py
```

10번은 최적화가 아니라 최종 검증입니다. 9-1에서 선택된 theta를 잠그고, 실제 목적지 3곳에 대해 B004/B04/B4를 비교합니다. 10번 안에서는 BO, CMA-ES, Random Search를 다시 실행하지 않고 theta도 새로 탐색하지 않습니다.

### theta 선택

기본 입력은 `09-1 B4 Optimization S1forced/outputs/latest.json`입니다. 제출용 검증에서는 full fixed-budget run의 `all_evaluations.csv`를 명시하는 편이 안전합니다.

theta 선택 정책:

- `final_status=PASS`인 row만 후보로 사용합니다.
- `--theta-method ALL`이면 Random Search, CMA-ES, BO 전체 PASS row 중 score가 가장 낮은 theta를 고릅니다.
- `--theta-method BO`, `CMA-ES`, `Random Search`로 특정 방법만 필터링할 수 있습니다.
- 고정 변수는 `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau` 5개입니다.
- `alpha`, `Q_trig`는 legacy 호환 필드이지 최종 theta 설명에 쓰지 않습니다.

`theta_source_smoke_warning=true`이면 smoke 산출물에서 theta를 읽은 것입니다. 이 경우 제출용 최종 결과라고 쓰면 안 됩니다.

### 목적지 선정

10번은 목적지 3개를 고정 목록으로 쓰지 않고, 최신 S1-forced 조건에서 다시 선별합니다.

1. `05_theta_check_simulation/routes/b0_valid_18_routes.csv`의 18개 target edge를 읽습니다.
2. Compact V9 소방서 출발 edge `420331801#1`에서 각 target edge까지 최신 net 기준 shortest route를 다시 만듭니다.
3. 최신 net에 target edge가 없거나 연결되지 않으면 `EXCLUDED_PRECHECK`로 기록합니다.
4. screening에서 실행 가능한 후보를 B004 1회, B04 1회, B4 1회 paired departure로 평가합니다.
5. EV 도착 성공, emergency teleport 없음, B004/B04/B4 비교 가능, B4 개선, Stage2/Stage3 실제 개입 조건을 만족하는 후보를 남깁니다.
6. 개선폭, B04 지연 크기, 실제 개입량, mainroad/spine 대표성 순서로 상위 3개를 최종 목적지로 확정합니다.

목적지 3개 선택에는 ESSI를 쓰지 않습니다. ESSI는 9-1 BO acquisition 구성요소이고, 10번 목적지 선택은 screening 결과의 개선폭, B04 지연, 개입량, 대표성 기준으로 유지합니다.

### 실행 phase

| phase | 동작 |
| --- | --- |
| `screening` | 18개 후보를 1회씩 평가해 최종 3개 후보를 고릅니다. |
| `final` | 선택된 3개 후보를 30회 반복 검증합니다. |
| `all` | screening과 final을 같은 run-id로 이어서 실행합니다. |

final phase의 기본 출발시각은 seed `20260606`으로 route/repeat별 deterministic random 값입니다. 범위는 `550s`부터 `650s`입니다. 최종 검증 runner는 candidate route 단위 병렬 실행을 지원하며, 기본 실행값은 `--workers 6`입니다. 한 candidate 내부의 B004/B04/B4 반복은 같은 worker에서 순차 실행됩니다.

### 실행

Dry-run:

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --dry-run \
  --run-id final_destination_dry_run
```

제출용 전체 실행:

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv" \
  --theta-method ALL \
  --workers 6 \
  --run-id final_destination_validation_001
```

screening만 먼저 실행:

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase screening \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv" \
  --workers 6 \
  --run-id final_destination_validation_001
```

같은 run-id의 screening 결과로 final만 실행:

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase final \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv" \
  --workers 6 \
  --run-id final_destination_validation_001
```

### 산출물

기본 위치는 `results/metrics/compact_v9_final_destination_validation/{run_id}/`입니다.

| 파일 | 의미 |
| --- | --- |
| `screening/candidate_selection.csv` | 18개 후보의 screening 결과와 제외/선정 사유 |
| `screening/task_manifest.csv` | screening에서 계획된 B004/B04/B4 task |
| `final/candidate_selection.csv` | 최종 3개 지점의 30-repeat 검증 요약 |
| `final/final_simulation_results.csv` | 제출/보고용 최종 시뮬레이션 clean CSV |
| `final/all_route_runs.csv` | final phase 전체 run row |
| `final/selected_route_runs.csv` | 선택된 목적지의 B004/B04/B4 run row |
| `final/selected_mode_averages.csv` | route/mode별 평균 지표 |
| `final/selected_destinations.json` | 실제 선택된 3개 지점, route edge, 출발시각, theta provenance |
| `final/spc_repeat_stability.csv` | final 30-repeat 결과의 route별 SPC 안정성 판단 |
| `final/final_destination_validation_report.md` | 선택된 3개 지점과 선택 이유 보고서 |
| `experiment_summary.json` | 전체 phase 요약 |

### 최종 결과를 설명할 때의 기준

- 10번은 theta를 읽어서 검증하는 단계입니다. 최적화 성능 비교는 9-1 산출물로 설명합니다.
- B004는 자유류 기준, B04는 no-control, B4는 잠근 theta를 적용한 제어 run입니다.
- final phase에서 목적지 3개가 각각 `1 B004 + 30 B04 + 30 B4` 구조로 실행됐을 때만 30-repeat 최종 검증이라고 설명합니다.
- 제출/보고용 clean CSV는 `output_D_E_sec`, `output_D_G_sec`, `weight_E`, `weight_G`, `weight_ratio`, `score` 순서로 score 직전에 weight를 둡니다.
- final phase SPC는 반복 결과 안정성 판단용입니다. report에는 route별 `stable`, `active`, `insufficient` 상태가 기록됩니다.
- smoke theta 또는 dry-run 산출물은 제출용 결과가 아닙니다.
- 최종 목적지 3개는 screening 결과에 의해 결정되므로, 실행 전 예비 후보명을 확정 결과처럼 쓰지 않습니다.

## 검증 명령

문서와 함께 확인할 최소 테스트는 다음입니다.

```bash
.venv/bin/python -m pytest tests/test_b4_optimization_s1forced.py tests/test_final_destination_validation.py -q
```

실제 SUMO 제출용 산출물 확인은 테스트가 아니라 각 runner의 full command 실행과 output row/PNG/report 확인으로 판단합니다.
