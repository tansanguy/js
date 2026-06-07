# For Taehoon: B4 S1-Forced 실행 흐름

이 문서는 태훈님이 로컬에서 그대로 복사해 실행할 수 있도록 정리한 실행 순서입니다.

흐름은 다음 순서입니다.

```text
환경 설정
-> 방법론 3개 실행: BO, Random Search, CMA-ES
-> 민감도 분석: Pareto weight sweep
-> 최종 분석: 목적지 screening + 최종 3개 목적지 30-repeat 검증
```

## 0. 공통 기준값

아래 값은 이번에 정한 기본 실행 예산입니다.

| 항목 | 값 | 설명 |
| --- | --- | --- |
| `N_SEEDS` | `15` | 방법별 seed 개수입니다. |
| `M_ROUNDS` | `50` | seed 하나당 평가할 theta 개수입니다. |
| `BO_INITIAL` | `10` | BO가 GP surrogate를 학습하기 전에 random observation으로 채우는 초기 평가 개수입니다. |
| `WORKERS` | `6` | 최적화 runner의 SUMO 병렬 평가 worker 수입니다. |
| `EI_CANDIDATES` | `600` | BO/ESSI가 다음 후보를 고를 때 sampling하는 후보 pool 크기입니다. |
| `SCREENING_REPEATS` | `1` | 최종 목적지 후보 18개를 1회씩 screening합니다. |
| `FINAL_REPEATS` | `30` | 최종 선택된 목적지 3개를 B04/B4 각각 30회 반복 검증합니다. |
| `FINAL_SELECTION_COUNT` | `3` | screening 후 최종 목적지 3개를 고릅니다. |

주의: 최적화 단계는 `WORKERS=6`을 기본으로 둡니다. 단, `10 Final Destination Validation/final_destination_validation.py`는 현재 코드에서 TraCI final validation을 `--workers 1`만 허용합니다. 그래서 최종 분석 명령만 `--workers 1`로 실행해야 합니다.

공통 입력은 최신 S1-forced 기준입니다.

```bash
export NET_FILE="09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml"
export BACKGROUND_ROUTE="data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml"
export STAGE1_DIR="data_prepared/compact_v9/b4_stage1_s1forced"

export N_SEEDS=15
export M_ROUNDS=50
export BO_INITIAL=10
export WORKERS=6
export EI_CANDIDATES=600

export METHOD_RUN_ID="taehoon_s1forced_methods_n15_m50"
export SENS_RUN_ID="taehoon_s1forced_sensitivity_m50"
export FINAL_RUN_ID="taehoon_final_destination_validation_001"
```

## 1. 환경 설정

프로젝트 루트로 이동합니다.

```bash
cd /Users/junlee/Desktop/js
```

가상환경을 만들고 dependency를 설치합니다.

```bash
bash 00_setup/setup_venv.sh
```

가상환경을 활성화합니다.

```bash
source .venv/bin/activate
```

SUMO, Python, `traci`, `sumolib`, `numpy`, `sklearn`, `cma`, `matplotlib` 설치 상태를 확인합니다.

```bash
python 00_setup/verify_env.py
```

혹시 package가 빠졌다고 나오면 requirements를 다시 설치합니다.

```bash
python -m pip install -r requirements.txt
```

실행 조건 감사도 먼저 돌립니다. 기대값은 `FAIL=0`, `WARN=0`, `INFO=0`입니다.

```bash
python "09-1 B4 Optimization S1forced/audit_09_run_conditions.py"
```

빠른 계약 테스트를 돌릴 때는 아래를 사용합니다.

```bash
python -m pytest tests/test_b4_09_run_conditions_audit.py -q
python -m pytest tests/test_b4_theta_bo.py tests/test_b4_stage1_contract.py tests/test_b4_runtime_contract.py -q
python -m pytest tests/test_b4_optimization_s1forced.py tests/test_final_destination_validation.py -q
```

## 2. 방법론 3개 실행

세 방법은 같은 `run-id`에 순서대로 누적합니다. 첫 번째 BO 실행이 결과 폴더를 만들고, Random Search와 CMA-ES는 `--append-existing`으로 같은 폴더에 추가합니다.

결과 폴더:

```text
09-1 B4 Optimization S1forced/outputs/${METHOD_RUN_ID}/
```

### 2-1. BO 실행

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id "$METHOD_RUN_ID" \
  --methods BO \
  --n "$N_SEEDS" \
  --m "$M_ROUNDS" \
  --bo-initial "$BO_INITIAL" \
  --workers "$WORKERS" \
  --ei-candidate-count "$EI_CANDIDATES" \
  --net-file "$NET_FILE" \
  --background-route "$BACKGROUND_ROUTE" \
  --stage1-dir "$STAGE1_DIR" \
  --hard-max-sim-time 4000 \
  --skip-pareto \
  --skip-noise-check
```

BO는 초기 `BO_INITIAL=10`개 theta를 random observation으로 평가한 뒤, GP surrogate와 ESSI acquisition으로 다음 theta를 고릅니다. `--skip-pareto`, `--skip-noise-check`는 여기서는 방법론 비교만 채우기 위해 민감도 분석과 5회 noise check를 빼는 옵션입니다.

### 2-2. Random Search 실행

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id "$METHOD_RUN_ID" \
  --methods "Random Search" \
  --append-existing \
  --n "$N_SEEDS" \
  --m "$M_ROUNDS" \
  --bo-initial "$BO_INITIAL" \
  --workers "$WORKERS" \
  --ei-candidate-count "$EI_CANDIDATES" \
  --net-file "$NET_FILE" \
  --background-route "$BACKGROUND_ROUTE" \
  --stage1-dir "$STAGE1_DIR" \
  --hard-max-sim-time 4000 \
  --skip-pareto \
  --skip-noise-check
```

Random Search는 theta 범위 안에서 seed별로 `M_ROUNDS=50`개 후보를 무작위로 평가합니다. BO처럼 surrogate를 학습하지 않으므로 비교 기준 baseline 역할입니다.

### 2-3. CMA-ES 실행

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id "$METHOD_RUN_ID" \
  --methods CMA-ES \
  --append-existing \
  --n "$N_SEEDS" \
  --m "$M_ROUNDS" \
  --bo-initial "$BO_INITIAL" \
  --workers "$WORKERS" \
  --ei-candidate-count "$EI_CANDIDATES" \
  --net-file "$NET_FILE" \
  --background-route "$BACKGROUND_ROUTE" \
  --stage1-dir "$STAGE1_DIR" \
  --hard-max-sim-time 4000 \
  --skip-pareto \
  --skip-noise-check
```

CMA-ES는 Python `cma` package의 `CMAEvolutionStrategy`를 사용합니다. 내부적으로 5개 결정변수 `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau`를 `[0, 1]` 정규화 공간에서 탐색한 뒤 실제 theta 범위로 되돌려 평가합니다.

### 2-4. 방법론 비교 산출물 확인

세 명령이 끝나면 아래 파일을 확인합니다.

| 파일 | 의미 |
| --- | --- |
| `all_evaluations.csv` | BO, Random Search, CMA-ES의 모든 theta 평가 row입니다. |
| `final_method_comparison_results.csv` | 제출용 clean CSV입니다. input theta, delay output, weight, score, 실측값, Stage2/Stage3 count만 남깁니다. |
| `table1_best_so_far.csv` | method/seed별 round 누적 best-so-far 표입니다. |
| `table2_bo_surrogate.csv` | BO 관측값, GP surrogate mean/CI, ESSI acquisition 표입니다. |
| `figure1_best_so_far.png` | 세 방법의 best-so-far 평균과 95% CI 그림입니다. |
| `figure2_bo_surrogate.png` | BO surrogate trace 그림입니다. |
| `experiment_summary.json` | 실행 입력, seed, 방법 목록, 산출물 manifest입니다. |

## 3. 민감도 분석

민감도 분석은 응급차 지연 `delay_A`와 일반차 지연 `delay_N`의 상대 가중치를 바꿔가며 Pareto 후보를 보는 단계입니다.

기본 weight ratio는 runner 내부에 고정된 아래 5개입니다.

```text
1:1, 5:1, 10:1, 15:1, 20:1
```

실행 명령:

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id "$SENS_RUN_ID" \
  --methods BO \
  --n 1 \
  --m "$M_ROUNDS" \
  --bo-initial "$BO_INITIAL" \
  --workers "$WORKERS" \
  --ei-candidate-count "$EI_CANDIDATES" \
  --net-file "$NET_FILE" \
  --background-route "$BACKGROUND_ROUTE" \
  --stage1-dir "$STAGE1_DIR" \
  --hard-max-sim-time 4000 \
  --skip-noise-check
```

여기서 `--n 1`은 민감도 분석 자체가 각 weight ratio마다 BO 탐색 1회를 수행하기 때문입니다. runner 구조상 `--methods BO`의 일반 BO도 같이 한 번 실행되고, 이어서 `run_pareto()`가 `1:1`, `5:1`, `10:1`, `15:1`, `20:1` 가중치별 BO sweep을 수행합니다.

민감도 산출물:

| 파일 | 의미 |
| --- | --- |
| `table3_pareto.csv` | weight ratio별 best theta, `delay_A`, `delay_N`, score, SPC 상태, knee 여부입니다. |
| `final_sensitivity_results.csv` | 제출용 clean CSV입니다. weight ratio별 best row만 정리합니다. |
| `table4_sensitivity_spc.csv` | weight ratio별 BO round의 `essi_log_max`, EWMA, SPC trace입니다. |
| `figure3_pareto.png` | Pareto 후보와 knee point 그림입니다. |
| `figure4_sensitivity_spc.png` | weight ratio별 ESSI log-max EWMA trace입니다. |

해석할 때 `10:1`이나 knee point를 정답이라고 쓰지 않습니다. 이 단계는 정책 결정자가 응급차 우선과 일반차 지연의 trade-off를 볼 수 있게 펼쳐 주는 분석입니다.

## 4. 최종 분석

최종 분석은 9-1 최적화에서 찾은 theta를 잠근 뒤 실제 목적지 후보를 검증하는 단계입니다. 이 단계에서는 BO, Random Search, CMA-ES를 다시 실행하지 않습니다.

절차:

```text
1. METHOD_RUN_ID의 all_evaluations.csv에서 PASS row 중 score가 가장 낮은 theta를 선택
2. 18개 목적지 후보를 최신 S1-forced net 기준으로 shortest route 재생성
3. screening phase에서 후보별 B004 1회, B04 1회, B4 1회 실행
4. EV 도착, teleport 없음, B4 개선, 실제 Stage2/Stage3 개입 조건으로 후보 필터링
5. 최종 목적지 3개 선택
6. final phase에서 각 목적지별 B004 reference 1회, B04 30회, B4 30회 실행
7. final repeat 결과에 SPC를 적용해 안정성 판단
```

실행 명령:

```bash
python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/${METHOD_RUN_ID}/all_evaluations.csv" \
  --theta-method ALL \
  --candidate-limit 18 \
  --screening-repeats 1 \
  --final-selection-count 3 \
  --repeats 30 \
  --depart-min 550 \
  --depart-max 650 \
  --seed 20260606 \
  --workers 1 \
  --run-id "$FINAL_RUN_ID" \
  --net "$NET_FILE" \
  --background-route "$BACKGROUND_ROUTE" \
  --base-stage1-dir "$STAGE1_DIR" \
  --hard-max-sim-time 4000
```

최종 분석 산출물:

| 파일 | 의미 |
| --- | --- |
| `screening/candidate_selection.csv` | 18개 후보 screening 결과와 선정/제외 사유입니다. |
| `final/candidate_selection.csv` | 최종 3개 목적지의 요약 결과입니다. |
| `final/final_simulation_results.csv` | 제출용 clean CSV입니다. B4 repeat row 중심으로 input/output/weight/score를 정리합니다. |
| `final/selected_route_runs.csv` | 최종 목적지의 B004/B04/B4 모든 run row입니다. |
| `final/selected_mode_averages.csv` | route/mode별 평균입니다. |
| `final/spc_repeat_stability.csv` | route/metric별 SPC 안정성 판단입니다. |
| `final/final_destination_validation_report.md` | 최종 보고서입니다. |

결과 위치:

```text
results/metrics/compact_v9_final_destination_validation/${FINAL_RUN_ID}/
runs/compact_v9_final_destination_validation/${FINAL_RUN_ID}/
```

## 5. 옵션 설명

| 옵션 | 적용 단계 | 설명 |
| --- | --- | --- |
| `--run-id` | 전체 | 결과 폴더 이름입니다. 같은 `run-id`를 쓰면 같은 output 디렉터리를 기준으로 읽고 씁니다. |
| `--methods` | 방법론 비교, 민감도 | 실행할 최적화 방법입니다. `BO`, `Random Search`, `CMA-ES`를 받을 수 있습니다. alias로 `bo`, `random`, `rs`, `cma`도 됩니다. |
| `--append-existing` | 방법론 비교 | 같은 `run-id`에 이미 있는 다른 방법 결과를 유지하고 새 방법 결과를 추가합니다. 이미 같은 method가 있으면 중복 방지를 위해 실패합니다. |
| `--n` | 방법론 비교 | 방법별 seed 개수입니다. 이번 기준은 `15`입니다. |
| `--m` | 방법론 비교, 민감도 | seed 하나당 평가 round 수입니다. 이번 기준은 `50`입니다. 민감도에서는 각 weight ratio별 BO round 수로도 쓰입니다. |
| `--bo-initial` | BO, 민감도 | BO 초기 random observation 수입니다. `2 <= bo_initial < m`이어야 합니다. 이번 기준은 `10`입니다. |
| `--workers` | 최적화 | SUMO 평가 병렬 worker 수입니다. 이번 최적화 기본값은 `6`입니다. 최종 목적지 검증은 현재 코드 제약으로 `1`만 지원합니다. |
| `--ei-candidate-count` | BO, 민감도 | GP/ESSI acquisition이 다음 theta를 고르기 위해 sampling하는 후보 수입니다. 값이 클수록 후보 탐색은 촘촘하지만 시간이 늘어납니다. |
| `--net-file` | 최적화 | S1-forced B04/B4 SUMO network 파일입니다. |
| `--net` | 최종 분석 | 최종 목적지 검증에서 사용할 SUMO network 파일입니다. 최적화 runner의 `--net-file`과 같은 의미입니다. |
| `--background-route` | 전체 | 배경 교통 수요 route XML입니다. |
| `--stage1-dir` | 최적화 | B4 Stage1 artifact 디렉터리입니다. |
| `--base-stage1-dir` | 최종 분석 | 목적지별 Stage1을 다시 만들 때 참조할 base Stage1 artifact 디렉터리입니다. |
| `--hard-max-sim-time` | 전체 | EV 미도착/정체 run이 너무 오래 걸리지 않도록 제한하는 최대 시뮬레이션 시간입니다. |
| `--skip-pareto` | 방법론 비교 | 방법론 3개를 따로 채울 때 민감도 분석을 생략합니다. |
| `--skip-noise-check` | 방법론 비교, 민감도 | 기준 theta 5회 반복 noise check를 생략합니다. 최종 30-repeat 검증과 혼동하지 않기 위해 별도 단계에서만 필요하면 켭니다. |
| `--theta-all-evaluations` | 최종 분석 | 최종 theta를 고를 `all_evaluations.csv` 경로입니다. |
| `--theta-method` | 최종 분석 | 최종 theta 후보를 어떤 방법에서 고를지 정합니다. `ALL`이면 BO, Random Search, CMA-ES 전체 PASS row 중 score 최저를 고릅니다. |
| `--candidate-limit` | 최종 분석 | screening할 목적지 후보 수입니다. 이번 기준은 `18`입니다. |
| `--screening-repeats` | 최종 분석 | screening phase 반복 수입니다. 이번 기준은 `1`입니다. |
| `--final-selection-count` | 최종 분석 | screening 후 최종 선택할 목적지 개수입니다. 이번 기준은 `3`입니다. |
| `--repeats` | 최종 분석 | final phase에서 B04/B4를 반복 실행할 횟수입니다. 이번 기준은 `30`입니다. |
| `--depart-min`, `--depart-max` | 최종 분석 | final validation의 emergency 출발 시각 sampling 범위입니다. 현재 기준은 550-650초입니다. |
| `--seed` | 최종 분석 | 목적지별 deterministic random departure 생성 seed입니다. 같은 seed를 쓰면 repeat별 출발 시각이 재현됩니다. |

## 6. 빠른 smoke 명령

전체 full run 전에 runner 계약만 빠르게 확인하려면 mock smoke를 돌립니다.

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --mock-eval \
  --run-id taehoon_mock_smoke \
  --n 2 \
  --m 5 \
  --bo-initial 2 \
  --workers "$WORKERS" \
  --ei-candidate-count 20
```

SUMO를 실제로 작게 돌리는 real smoke는 아래입니다.

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id taehoon_real_smoke \
  --n 1 \
  --m 4 \
  --bo-initial 2 \
  --workers "$WORKERS" \
  --ei-candidate-count 50 \
  --net-file "$NET_FILE" \
  --background-route "$BACKGROUND_ROUTE" \
  --stage1-dir "$STAGE1_DIR" \
  --hard-max-sim-time 4000 \
  --skip-pareto \
  --skip-noise-check
```

## 7. 결과 확인 요약

방법론 비교가 끝나면 우선 이 파일을 봅니다.

```text
09-1 B4 Optimization S1forced/outputs/${METHOD_RUN_ID}/final_method_comparison_results.csv
09-1 B4 Optimization S1forced/outputs/${METHOD_RUN_ID}/figure1_best_so_far.png
```

민감도 분석이 끝나면 이 파일을 봅니다.

```text
09-1 B4 Optimization S1forced/outputs/${SENS_RUN_ID}/final_sensitivity_results.csv
09-1 B4 Optimization S1forced/outputs/${SENS_RUN_ID}/figure3_pareto.png
09-1 B4 Optimization S1forced/outputs/${SENS_RUN_ID}/figure4_sensitivity_spc.png
```

최종 분석이 끝나면 이 파일을 봅니다.

```text
results/metrics/compact_v9_final_destination_validation/${FINAL_RUN_ID}/final/final_destination_validation_report.md
results/metrics/compact_v9_final_destination_validation/${FINAL_RUN_ID}/final/final_simulation_results.csv
results/metrics/compact_v9_final_destination_validation/${FINAL_RUN_ID}/final/spc_repeat_stability.csv
```
