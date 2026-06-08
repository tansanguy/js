# B4 Optimization S1-forced 실행 요약

이 폴더는 최신 S1-forced B04/B4 입력 묶음으로 Random Search, 표준 `cma` 패키지 기반 CMA-ES, Bayesian Optimization을 fixed-budget 조건에서 비교하는 runner를 담고 있습니다. BO는 `GP+ESSI` 기반 실제 최적화로 실행합니다.

## Canonical Inputs

| 항목 | 값 |
| --- | --- |
| profile | `B04_B4_S1_FORCED_OPTIMIZATION` |
| net | `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml` |
| demand | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml` |
| Stage1 | `data_prepared/compact_v9/b4_stage1_s1forced` |
| Stage1 measurement source | `B04_ad_stage23_trigger` |
| active inputs | `configs/compact_v9_B04_B4_active_inputs.json` |
| decision variables | `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau` |
| 기본 score | `(10/11) * D_E + (1/11) * D_G` |

`D_E`는 응급차 자유류 대비 지연입니다. `D_G`는 `V_G` 영향권 일반차 대당 평균 지연이며, `V_G`는 본선 route edge와 본선 교차로 TLS의 SUMO `.net.xml` incoming edge를 합쳐 자동 구성합니다. 산출물의 `D_E_sec`는 `D_E`, `D_G_sec`은 `D_G`에 대응합니다.

## 1. 실행 조건 감사

```bash
python "09-1 B4 Optimization S1forced/audit_09_run_conditions.py"
```

JSON 보고서:

```bash
python "09-1 B4 Optimization S1forced/audit_09_run_conditions.py" --json
```

정상 기대값은 `FAIL=0`, `WARN=0`, `INFO=0`입니다.

## 2. 환경 확인

```bash
python 00_setup/verify_env.py
```

빠진 패키지가 있으면:

```bash
python -m pip install -r requirements.txt
```

## 3. Mock Contract

SUMO 없이 CSV/PNG 산출물 계약을 확인합니다.

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

## 4. Real Smoke

실제 SUMO를 작게 돌려 full run 전에 실패 조건을 확인합니다.

현재 확인 상태:

- `audit_09_run_conditions.py`는 PASS입니다.
- S1-forced canonical 신호망/수요의 `b04-validate --candidates B04_ad_stage23_trigger`는 현재 `WARN`입니다. `emergency_arrived=true`, `emergency_teleport=false`, `background_teleported=0`, `speed_sanity_fail_count=0`, `metric_invalid_count=0`, `speed_mae_kmh=4.773`, `travel_time_mae_s=23.35`, `queue_top10_overlap=4`입니다. `queue_top10_overlap`은 병목 위치 정합성 진단값이며 preflight 실패 조건으로 사용하지 않습니다.
- 이 runner의 real smoke는 S1-forced baseline gate를 통과합니다. baseline row는 `final_status=PASS`, `termination_reason=ev_arrived_min_summary`, `T_actual_EMV_sec=451.0`, `emergency_arrived=true`, `emergency_teleport=false`, `background_teleported=0`입니다.
- `s1forced_queue_overlap_relaxed_smoke`의 `n=1`, `m=4`, `--skip-pareto`, `--skip-noise-check` smoke는 baseline gate를 통과했고 `all_evaluations.csv`, `table1_best_so_far.csv`, `table2_bo_surrogate.csv`, `figure1_best_so_far.png`, `figure2_bo_surrogate.png`를 생성했습니다. 평가 12개 중 11개는 `PASS`, BO 초기 theta 1개는 `emergency_stuck`으로 `FAIL`입니다.

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

## 5. Fixed-Budget 본 실행

기본 예산은 `n=1`, `m=50`, `theta_per_round=6`, `workers=6`입니다. 한 round는 theta 6개를 병렬 평가합니다.

BO는 초기 `bo_initial` round를 random observation으로 채운 뒤 GP surrogate로 후보별 개선 가능성을 계산합니다. 이후 최종 선택은 일반 EI가 아니라 subspace별 ESSI acquisition만 사용합니다.

```text
ESSI_i(theta) = GP_improvement(theta) * spatial_subspace_activation_i(theta)
essi_acquisition(theta) = max_i ESSI_i(theta)
```

ESSI는 Stage1 route movement를 route-order 기준 6개 spatial subspace로 나누고 controllable movement density, Case B candidate count, bottleneck/control candidate presence를 반영한 weight로 subspace별 개선 가능성을 계산합니다. GP/ESSI 후보 생성에 실패하면 random fallback 없이 실패를 드러냅니다.

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id s1forced_fixed_budget_n1_m50_t6 \
  --n 1 \
  --m 50 \
  --theta-per-round 6 \
  --bo-initial 10 \
  --workers 6 \
  --essi-candidate-count 600 \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml" \
  --stage1-dir "data_prepared/compact_v9/b4_stage1_s1forced" \
  --hard-max-sim-time 4000
```

## 6. Pareto 가중치 Sweep

`table3_pareto.csv`, `table4_sensitivity_spc.csv`, `figure3_pareto.png`, `figure4_sensitivity_spc.png`는 가중치별 Pareto 후보와 ESSI/SPC trace를 펼쳐 보여주기 위한 산출물입니다. 가중치를 자동으로 정하는 것이 목적이 아닙니다.

보여줄 내용:

| 가중치(w1:w2) | 최적 theta | D_E_sec | D_G_sec |
| --- | --- | --- | --- |
| 1:1 | 결과 CSV 값 | 결과 CSV 값 | 결과 CSV 값 |
| 5:1 | 결과 CSV 값 | 결과 CSV 값 | 결과 CSV 값 |
| 10:1 | 결과 CSV 값 | 결과 CSV 값 | 결과 CSV 값 |
| 15:1 | 결과 CSV 값 | 결과 CSV 값 | 결과 CSV 값 |
| 20:1 | 결과 CSV 값 | 결과 CSV 값 | 결과 CSV 값 |

실행 원칙:

- 각 가중치에서 net, demand, Stage1, 사고 위치, 출동 조건은 모두 동일하게 둡니다.
- 각 가중치에 대해 ESSI-aware BO 탐색 1회를 수행합니다.
- 기본 Pareto sweep은 ESSI/SPC trace가 안정화되면 조기 중단할 수 있습니다.
- 값이 튀는 경우에만 같은 가중치의 반복 탐색을 추가합니다.
- 실제 30회 반복을 수행하지 않았다면 30회 반복 결과라고 쓰지 않습니다.

`figure3_pareto.png`의 주황색 점은 knee point 보조 표시입니다. 10:1이 정답이라는 말도 아니고, knee point 가중치를 반드시 채택해야 한다는 결론도 아닙니다. 최종 결정은 정책 결정자의 몫입니다.

## 7. 주요 옵션

| 옵션 | 의미 |
| --- | --- |
| `--run-id` | `outputs/{run_id}` 결과 디렉터리 이름입니다. |
| `--n` | 방법별 seed 개수입니다. 기본값은 1입니다. |
| `--m` | seed 하나당 평가 round 수입니다. |
| `--theta-per-round`, `--solutions-per-round`, `--batch-size` | round당 theta 후보 수입니다. 총 평가는 방법/seed별 `m * theta_per_round`개입니다. |
| `--bo-initial` | BO 초기 random observation 수입니다. |
| `--workers` | round 내부 theta 평가 병렬 수입니다. 기본 `--theta-per-round 6 --workers 6`이면 한 round의 theta 6개가 동시에 실행됩니다. |
| `--w-E`, `--w1` | 응급차 delay 가중치입니다. 기본값은 10입니다. |
| `--w-G`, `--w2` | 일반차 delay 가중치입니다. 기본값은 1입니다. |
| `--essi-candidate-count`, `--ei-candidate-count` | ESSI 후보 sampling 수입니다. 기존 호환을 위해 `--ei-candidate-count` alias도 유지합니다. |
| `--methods` | 이번 실행에서 돌릴 방법만 지정합니다. `bo`, `random`, `cma` alias를 쓸 수 있습니다. |
| `--bo-first` | 한 번의 실행에서 BO를 먼저 돌리고 나머지 선택 방법을 뒤에 실행합니다. |
| `--append-existing` | 같은 `run-id`의 기존 `all_evaluations.csv`를 읽고 이번 method 결과를 merge합니다. 이미 존재하는 method를 다시 append하면 실패합니다. |
| `--resume`, `--bo-resume` | 중단된 같은 `run-id`를 `checkpoints/*.csv`와 기존 `all_evaluations.csv`에서 이어 실행합니다. |
| `--skip-pareto` | Pareto 가중치 sweep을 생략합니다. |
| `--no-pareto-spc-stop` | Pareto sweep에서 SPC 기반 조기 중단을 끄고 round 수를 채웁니다. |
| `--skip-noise-check` | 5회 noise check를 생략합니다. |
| `--mock-eval` | SUMO 대신 mock evaluator를 사용합니다. |

### BO를 먼저 따로 실행하고 나머지 방법론 이어붙이기

1차로 BO만 실행합니다.

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id s1forced_staged_bo_first \
  --methods bo \
  --n 1 \
  --m 50 \
  --theta-per-round 6 \
  --workers 6 \
  --bo-initial 10 \
  --essi-candidate-count 600 \
  --skip-pareto \
  --skip-noise-check
```

BO가 끝난 뒤 같은 `run-id`에 Random Search와 CMA-ES만 append합니다.

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id s1forced_staged_bo_first \
  --methods random cma \
  --append-existing \
  --n 1 \
  --m 50 \
  --theta-per-round 6 \
  --workers 6 \
  --bo-initial 10 \
  --essi-candidate-count 600 \
  --skip-pareto \
  --skip-noise-check
```

한 번의 command에서 BO를 먼저 돌리고 나머지를 뒤에 실행하려면 `--bo-first`를 씁니다.

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id s1forced_bo_first_single_run \
  --bo-first \
  --n 1 \
  --m 50 \
  --theta-per-round 6 \
  --workers 6 \
  --bo-initial 10 \
  --essi-candidate-count 600
```

## 8. 산출물

`outputs/{run_id}/` 아래에 생성됩니다.

| 파일 | 의미 |
| --- | --- |
| `all_evaluations.csv` | 전체 theta 평가 결과입니다. |
| `final_method_comparison_results.csv` | 제출/보고용 방법론 비교 clean CSV입니다. input, 목적함수 output 2개, weight, score, 실측값, Stage2/Stage3 on 횟수만 기록합니다. |
| `table1_best_so_far.csv` | `method, seed, R1...Rm` 형식의 누적 최솟값 표입니다. |
| `table2_bo_surrogate.csv` | BO 관측값, best-so-far, surrogate mean/CI, ESSI acquisition 표입니다. |
| `table3_pareto.csv` | 가중치별 `weight_ratio`, 5개 theta, `D_E_sec`, `D_G_sec`, `score`, SPC 중단 정보, knee 표시를 기록합니다. |
| `table4_sensitivity_spc.csv` | 가중치별 BO round의 ESSI/SPC trace입니다. |
| `final_sensitivity_results.csv` | 제출/보고용 민감도 clean CSV입니다. 각 가중치별 best row만 같은 clean 컬럼 계약으로 기록합니다. |
| `bo_spatial_subspaces.json` | ESSI용 6개 spatial subspace 정의와 weight입니다. |
| `figure1_best_so_far.png` | 방법별 best-so-far 평균과 95% CI입니다. |
| `figure2_bo_surrogate.png` | BO surrogate trace입니다. |
| `figure3_pareto.png` | Pareto/knee 후보 그림입니다. |
| `figure4_sensitivity_spc.png` | Pareto sensitivity의 ESSI/SPC trace 그림입니다. |
| `noise_check_5repeat.csv` | 실제 5회 noise check입니다. |
| `experiment_summary.json` | 입력, 예산, seed, 산출물 manifest입니다. |

## 9. 해석 주의

- CMA-ES는 Python `cma` 패키지의 `CMAEvolutionStrategy`를 사용합니다.
- fixed-budget 비교의 BO는 `GP+ESSI`로 실제 theta를 찾습니다.
- fixed-budget 비교 산출물에는 SPC stop/status 필드를 넣지 않습니다. SPC는 Pareto sensitivity에서만 사용합니다.
- `table2_bo_surrogate.csv`와 BO row의 `all_evaluations.csv`에는 `essi_acquisition`, `essi_1`부터 `essi_6`, `essi_max`, `essi_mean`, `essi_log_max`, `dominant_essi_subspace`, `spatial_activation_score`가 기록됩니다.
- 제출/보고용 clean CSV에서는 `output_D_E_sec`, `output_D_G_sec` 뒤에 `weight_E`, `weight_G`, `weight_ratio`를 두고 그 바로 오른쪽에 `score`를 둡니다.
- Pareto sweep은 가중치별 trade-off를 제시하는 절차입니다. 가중치 선택은 정책 결정자의 몫입니다.
- noise check는 실제 5회 반복 artifact입니다. 30회 반복을 수행하지 않았다면 30회라고 설명하지 않습니다.
- full `n=1`, `m=50`, `theta_per_round=6` 결과가 제출 가능한지는 실제 run 후 CSV/PNG 존재와 row 수를 확인해야 판단합니다.
