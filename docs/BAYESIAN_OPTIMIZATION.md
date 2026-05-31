# Bayesian Optimization 실행 안내

표준 BO workflow는 `--bo-stage loop`다. 이 모드는 기존 관측 결과를 학습하고, `5개 theta 추천 -> B00/B2 실행 -> 결과 재학습`을 10라운드 반복한다.

## 명령어 옵션 빠른 설명

| 옵션 | 쉬운 뜻 |
| --- | --- |
| `--bo-stage loop` | 표준 자동 batch BO다. 추천, SUMO 실행, 재학습을 round 단위로 반복한다. |
| `--bo-rounds 10` | BO round 수다. 기본은 10라운드다. |
| `--bo-batch-size 5` | 한 round에서 추천할 theta 개수다. 기본은 5개다. |
| `--bo-eval-repeats 5` | 추천 theta를 실제 시뮬레이션에서 몇 번 반복할지 정한다. |
| `--bo-batch-strategy cl_min` | `scikit-optimize`의 batch ask 전략이다. 기본은 constant liar minimum이다. |
| `--bo-auto-inputs` | `state.json`과 `latest.json`에서 이전 결과 CSV를 자동으로 찾는다. |
| `--bo-initial-results PATH ...` | 자동 입력 대신 학습 시작용 결과 CSV를 직접 지정한다. |
| `--bo-resume` | 중단된 loop를 `state.json`의 마지막 완료 round 이후부터 이어서 실행한다. |
| `--workers N` | round 내부 B00/B2 평가를 병렬 실행할 worker 수다. |
| `--bo-eval-output-prefix` | round별 실제 SUMO 결과가 저장될 prefix다. 기본은 `parameter_input_sim_bo_eval`. |
| `--bo-mock-eval` | SUMO 없이 mock 결과로 loop 구조만 검증한다. 테스트/개발용이다. |

기존 `--bo-stage init/suggest/top3`와 `--bayesian true`는 수동 진단/호환용으로 남긴다. 새 표준 절차에서는 `loop`를 사용한다.

## 모델

- 라이브러리: `scikit-optimize`
- 모델: `skopt.Optimizer(base_estimator="GP", acq_func="EI")`
- batch 추천: `ask(n_points=5, strategy="cl_min")`
- 변수:
  - `D_det`: `300, 350, ..., 1000`
  - `alpha`: `0, 1, ..., 15`
  - `G_ext`: `10, 15, ..., 60`
- 고정값: `T_change_sec=10`
- target:

```text
bo_score_sec = score_sec + signal_burden_penalty_sec + failure_penalty_sec
score_sec = 3*A_delay_sec + N_delay_sec + T_recovery_sec
signal_burden_penalty_sec = 0.5*total_extension_delta_sec + 30*phase_switch_count
```

`score_sec`는 연구 기본 점수로 유지한다. BO는 신호 부담까지 포함한 `bo_score_sec`를 낮추는 방향으로 theta를 고른다.

## 표준 실행

기존 22-row candidate summary에서 시작하는 기본 실행:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage loop \
  --bo-initial-results results/metrics/parameter_input_sim/initial_observations/parameter_input_sim_candidate_summary.csv \
  --bo-rounds 10 \
  --bo-batch-size 5 \
  --bo-eval-repeats 5 \
  --workers 6 \
  --manifest configs/final_experiment_manifest.json
```

최신 결과를 자동으로 이어 쓰는 실행:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage loop \
  --bo-auto-inputs \
  --bo-rounds 10 \
  --bo-batch-size 5 \
  --bo-eval-repeats 5 \
  --workers 6 \
  --manifest configs/final_experiment_manifest.json
```

중단 후 이어서 실행:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage loop \
  --bo-resume \
  --bo-rounds 10 \
  --bo-batch-size 5 \
  --bo-eval-repeats 5 \
  --workers 6 \
  --manifest configs/final_experiment_manifest.json
```

## Round 순환 구조

각 round는 다음 순서로 돈다.

1. 누적 B2 관측치를 theta별 평균 `bo_score_sec`로 집계한다.
2. `Optimizer.tell(X, y)`로 GP surrogate를 현재 결과에 맞춘다.
3. `ask(n_points=5, strategy="cl_min")`로 새 theta 5개를 추천한다.
4. 이미 실행한 theta와 batch 내부 중복은 제거한다.
5. `configs/generated/b2_bo_round_{loop_run_id}_r{round}.csv`를 만든다.
6. 같은 러너를 subprocess로 호출해 `B00 B2`를 실행한다.
7. round 결과의 `experiment_results.csv`를 다시 읽어 다음 round 학습 데이터에 추가한다.

한 round에서 유효 B2 관측치가 하나도 없으면 loop는 중단되고 `FAIL_NO_VALID_OBSERVATIONS_IN_ROUND`로 기록된다.

## 산출물

BO workflow 결과:

```text
results/metrics/parameter_input_sim_bo/{loop_run_id}/
  bo_loop_summary.json
  bo_loop_state.json
  bo_rounds.csv
  bo_all_results.csv
  bo_observations.csv
  bo_excluded_observations.csv
  bo_recommendations_round_01.csv
  ...
```

round별 실제 SUMO 결과:

```text
results/metrics/parameter_input_sim_bo_eval/{sim_run_id}/experiment_results.csv
```

최신 BO 상태:

```text
results/metrics/parameter_input_sim_bo/latest.json
results/metrics/parameter_input_sim_bo/state.json
```

loop 완료 또는 중간 상태에서 top3 재평가 CSV도 생성된다.

```text
configs/generated/b2_bo_top3_reeval_{loop_run_id}.csv
```

## 결과 해석

- `bo_rounds.csv`: round별 추천 CSV, 평가 결과 CSV, best theta를 확인한다.
- `bo_all_results.csv`: round 0 초기 관측과 round 1,2,...의 모든 theta 결과를 한 파일에서 확인한다.
- `bo_observations.csv`: GP 학습에 들어간 유효 관측치다.
- `bo_excluded_observations.csv`: 실패, 미도착, teleport, route error 등으로 제외된 row다.
- `bo_loop_summary.json`: 전체 loop 상태, 완료 round, best theta, 최종 status를 본다.
- round별 `experiment_results.csv`: 실제 B00/B2 실행 결과 원본이다.

컬럼별 의미는 [RESULT_REVIEW_GUIDE.md](RESULT_REVIEW_GUIDE.md)를 따른다.

## Smoke Test

SUMO 없이 loop 구조만 확인:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage loop \
  --bo-initial-results results/metrics/parameter_input_sim/initial_observations/parameter_input_sim_candidate_summary.csv \
  --bo-rounds 1 \
  --bo-batch-size 5 \
  --bo-eval-repeats 1 \
  --bo-output-prefix parameter_input_sim_bo_smoke \
  --bo-workflow-prefix parameter_input_sim_bo_smoke \
  --bo-eval-output-prefix parameter_input_sim_bo_smoke_eval \
  --bo-mock-eval
```

기존 수동 추천 모드 확인:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage suggest \
  --bo-initial-results results/metrics/parameter_input_sim/initial_observations/parameter_input_sim_candidate_summary.csv \
  --bo-recommend-count 3
```
