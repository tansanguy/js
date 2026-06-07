# B4 현재 최적화 흐름

작성 기준: 2026-06-07 현재 코드베이스

이 문서는 현재 코드에 구현된 최적화 흐름을 세 갈래로 나눠 설명합니다.

1. Bayesian Optimization과 다른 방법론 비교
2. Bayesian Optimization 단일 실행
3. Pareto 가중치 sweep

## 공통 입력

최신 실행 조건은 `B04_B4_S1_FORCED_OPTIMIZATION` profile입니다.

| 항목 | 값 |
| --- | --- |
| net | `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml` |
| demand | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml` |
| Stage1 | `data_prepared/compact_v9/b4_stage1_s1forced` |
| Stage1 measurement source | `B04_ad_stage23_trigger` |
| active manifest | `configs/compact_v9_B04_B4_active_inputs.json` |
| 결정변수 | `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau` |
| 기본 score | `(10/11) * delay_A + (1/11) * delay_N` |

## 1. BO와 다른 방법론 비교

정본 runner:

```text
09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py
```

목적은 Random Search, 표준 `cma` 패키지 기반 CMA-ES, Bayesian Optimization을 같은 fixed-budget 조건에서 비교하는 것입니다.

### 실행 구조

1. `preflight()`
   - net, demand, Stage1 디렉터리 존재 여부를 확인합니다.
   - Stage1의 `decision_variables`가 5개인지 확인합니다.
   - active manifest의 net/demand/stage1 경로가 실행 인자와 일치하는지 확인합니다.

2. `run_method("Random Search")`
   - theta search space에서 무작위 후보를 `m`개 뽑습니다.
   - seed별로 누적 best-so-far를 기록합니다.

3. `run_method("CMA-ES")`
   - Python `cma` 패키지의 `CMAEvolutionStrategy`를 사용합니다.
   - 정규화된 theta vector `[0, 1]^5` 공간에서 후보를 묻고, 실제 theta 범위로 되돌려 SUMO/mock 평가를 수행합니다.

4. `run_method("BO")`
   - 초기 `bo_initial` round는 random theta로 채웁니다.
   - 이후 round는 기존 관측값으로 Gaussian Process surrogate를 만들고 Expected Improvement 후보 중 하나를 평가합니다.
   - ESSI/SPC 진단 필드는 기록하지만 fixed-budget 비교에서는 조기종료하지 않고 `m` round를 채웁니다.

5. `run_pareto()`
   - `1:1`, `5:1`, `10:1`, `15:1`, `20:1` 가중치 ratio별 BO 탐색을 수행합니다.
   - 같은 net, demand, Stage1, 사고/출동 조건에서 delay_A와 delay_N의 trade-off를 기록합니다.
   - knee point 후보를 보조 표시합니다.

6. `run_noise_check()`
   - 기준 theta 1개를 실제 5회 반복합니다.
   - 이 artifact는 5회 반복입니다. 30회 반복으로 설명하면 안 됩니다.

### 기본 실행

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id s1forced_fixed_budget_n15_m50 \
  --n 15 \
  --m 50 \
  --bo-initial 10 \
  --workers 6 \
  --ei-candidate-count 600 \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml" \
  --stage1-dir "data_prepared/compact_v9/b4_stage1_s1forced" \
  --hard-max-sim-time 4000
```

### 주요 옵션

| 옵션 | 의미 |
| --- | --- |
| `--n` | 방법별 seed 개수입니다. 기본값은 15입니다. |
| `--m` | seed 하나당 평가할 theta 수입니다. 기본값은 50입니다. |
| `--bo-initial` | BO 초기 random observation 개수입니다. |
| `--workers` | SUMO 평가 병렬 worker 수입니다. |
| `--w-emv`, `--w1` | 응급차 delay 가중치입니다. 기본값은 10입니다. |
| `--w-veh`, `--w2` | 일반차 delay 가중치입니다. 기본값은 1입니다. |
| `--ei-candidate-count` | Expected Improvement 후보 sampling 개수입니다. |
| `--skip-pareto` | Pareto sweep을 생략합니다. |
| `--no-pareto-spc-stop` | Pareto sweep에서 SPC 기반 조기 중단을 끄고 round 수를 채웁니다. |
| `--skip-noise-check` | 5회 noise check를 생략합니다. |
| `--mock-eval` | SUMO 대신 mock evaluator를 사용합니다. |

### 산출물

| 파일 | 의미 |
| --- | --- |
| `all_evaluations.csv` | 전체 평가 결과입니다. |
| `table1_best_so_far.csv` | 방법별/seed별 누적 best-so-far 표입니다. |
| `table2_bo_surrogate.csv` | BO surrogate, CI, acquisition long-form 표입니다. |
| `table3_pareto.csv` | 가중치별 theta, `delay_A`, `delay_N`, SPC/knee 정보를 담은 표입니다. |
| `figure1_best_so_far.png` | 방법별 best-so-far 평균과 95% CI입니다. |
| `figure2_bo_surrogate.png` | BO 관측값, best-so-far, surrogate mean/CI입니다. |
| `figure3_pareto.png` | Pareto/knee 후보 그림입니다. |
| `noise_check_5repeat.csv` | 기준 theta 5회 반복 결과입니다. |
| `experiment_summary.json` | 실행 입력, 예산, seed, 산출물 manifest입니다. |

## 2. Bayesian Optimization 단일 실행

정본 runner:

```text
09 Compact Corridor Baseline/run_b4_theta_bo.py
```

이 흐름은 기존 단일 BO runner입니다. 5개 결정변수, S1-forced 입력, 지연 우선 정규화 score 정책은 최종 runner와 맞췄습니다. 다만 방법론 비교 표/그림의 정본은 `09-1` fixed-budget runner입니다.

### 실행 구조

1. initial design 생성
   - `--initial-count` 개수만큼 초기 theta를 만듭니다.

2. loop
   - `--bo-rounds` round 동안 surrogate를 갱신합니다.
   - round마다 `--bo-batch-size` 후보를 추천/평가합니다.

3. resume
   - `latest.json`, `state.json` 기반으로 중단된 BO를 이어갈 수 있습니다.

4. ESSI/SPC
   - BO 진단과 조기종료 추천에 쓰는 필드를 기록합니다.
   - `--spc-stop` 옵션으로 조기종료 정책을 켤 수 있습니다.

### 실행 예시

```bash
python "09 Compact Corridor Baseline/run_b4_theta_bo.py" \
  --run-id b4_theta_bo_001 \
  --initial-count 20 \
  --bo-rounds 10 \
  --bo-batch-size 5 \
  --repeats 1 \
  --workers 6 \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml" \
  --stage1-dir "data_prepared/compact_v9/b4_stage1_s1forced" \
  --hard-max-sim-time 4000
```

### 주의

- `run_b4_theta_bo.py`의 `score_for_row()`는 `d_EMV_sec`, `d_veh_sec`를 우선 사용하고, ratio 입력을 `w/(w_E+w_G)`로 정규화합니다.
- 최종 방법론 비교 표/그림에는 `09-1` fixed-budget runner 산출물을 사용합니다.

## 3. Pareto 가중치 Sweep

여기서 말하는 민감도 분석은 가중치를 바꿔가며 응급차 지연과 일반차 지연이 어떻게 맞교환되는지 보여주는 Pareto sweep입니다. 목적은 가중치를 정하는 것이 아니라, 정책 결정자에게 선택지를 펼쳐 보여주는 것입니다.

### 구현 위치

```text
09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py::run_pareto()
```

### 표 양식

| 가중치(w1:w2) | 최적 theta | delay_A | delay_N |
| --- | --- | --- | --- |
| 1:1 | `table3_pareto.csv` | `table3_pareto.csv` | `table3_pareto.csv` |
| 5:1 | `table3_pareto.csv` | `table3_pareto.csv` | `table3_pareto.csv` |
| 10:1 | `table3_pareto.csv` | `table3_pareto.csv` | `table3_pareto.csv` |
| 15:1 | `table3_pareto.csv` | `table3_pareto.csv` | `table3_pareto.csv` |
| 20:1 | `table3_pareto.csv` | `table3_pareto.csv` | `table3_pareto.csv` |

`table3_pareto.csv`에는 추가로 `score`, `rounds_completed`, `spc_stop_recommended`, `spc_stop_round`, `is_knee`가 기록됩니다.

### 실행 원칙

- 가중치 외의 조건은 모두 동일하게 둡니다.
- 각 가중치에 대해서 BO 탐색 1회를 수행합니다.
- 기본 Pareto sweep은 SPC 기반으로 개선 변동이 잦아지는 지점에서 중단할 수 있습니다.
- 값이 튀는 경우에만 같은 가중치의 반복 탐색을 추가합니다.
- 실제 반복 실행을 하지 않았다면 30회 반복 결과라고 쓰지 않습니다.

### 해석

1. 가중치 변화에 따라 `delay_A`와 `delay_N`이 맞교환되는 정도를 봅니다.
2. 붉은 knee point는 합리적인 후보 지점을 설명하기 위한 보조 표시입니다.
3. knee point나 10:1을 정답으로 결론 내리지 않습니다.
4. 최종 결정은 정책 결정자의 몫입니다.
