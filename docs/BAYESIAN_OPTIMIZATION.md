# Bayesian Optimization 실행 안내

이 문서는 B2 신호 제어 파라미터 `D_det`, `alpha`, `G_ext`를 최적화하는 표준 절차다. `T_change_sec`는 `10s`로 고정한다.

BO의 target은 `bo_score_sec`다.

```text
bo_score_sec = score_sec + signal_burden_penalty_sec + failure_penalty_sec
score_sec = 3*A_delay_sec + N_delay_sec + T_recovery_sec
signal_burden_penalty_sec = 0.5*total_extension_delta_sec + 30*phase_switch_count
```

`score_sec`는 기존 연구 지표로 유지하고, BO는 긴 green extension과 phase switch 부담까지 포함한 `bo_score_sec`를 낮추는 방향으로 추천한다.

## Step 1. 초기 theta 생성

명령어:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage init \
  --bo-initial-count 20 \
  --bo-sampler sobol
```

입력:

- 없음

출력:

- `configs/generated/b2_bo_initial_{run_id}.csv`
- `results/metrics/parameter_input_sim_bo/{run_id}/bo_initial_parameters.csv`
- `results/metrics/parameter_input_sim_bo/latest.json`
- `results/metrics/parameter_input_sim_bo/state.json`
- `results/metrics/parameter_input_sim_bo/{run_id}/bo_commands.sh`

다음 단계에서 쓸 파일:

- `configs/generated/b2_bo_initial_{run_id}.csv`
- `{run_id}`는 직접 외울 필요가 없다. `bo_commands.sh`에 실제 경로가 들어간다.

## Step 2. 초기 theta 실행

Step 1에서 생성된 `bo_commands.sh`의 첫 번째 시뮬레이션 명령을 실행한다. 파일 경로는 `latest.json`에서 자동으로 확인할 수 있다.

```bash
python - <<'PY'
import json
from pathlib import Path
latest = json.loads(Path("results/metrics/parameter_input_sim_bo/latest.json").read_text())
print(Path(latest["bo_commands_sh"]).read_text())
PY
```

위 출력의 첫 번째 `python 02_simulation/run_b0_b1_b2_experiment.py ... --b2-params ...` 명령을 실행한다. 직접 실행 형식은 아래와 같다.
`bo_commands.sh` 전체를 바로 실행하면 뒤의 suggest/top3 명령까지 이어서 실행될 수 있으므로, 표준 절차에서는 단계별로 필요한 명령만 실행한다.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B2 \
  --b2-params configs/generated/b2_bo_initial_{run_id}.csv \
  --repeats 5 \
  --workers 1 \
  --emergency-depart 600 \
  --timeout-steps 7200 \
  --recovery-buffer-sec 300
```

입력:

- 초기 theta CSV

출력:

- `results/metrics/parameter_input_sim/{sim_run_id}/experiment_results.csv`
- `results/metrics/parameter_input_sim/latest.json`

다음 단계에서 쓸 파일:

- 자동 모드에서는 `latest.json`을 읽으므로 수동 입력이 필요 없다.

## Step 3. 누적 결과로 다음 theta 추천

명령어:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage suggest \
  --bo-auto-inputs \
  --bo-recommend-count 5
```

입력:

- `results/metrics/parameter_input_sim_bo/state.json`
- `results/metrics/parameter_input_sim/latest.json`

자동 입력 규칙:

- `state.json.latest_results_csvs`를 먼저 읽는다.
- `results/metrics/parameter_input_sim/latest.json`의 `results_csv`를 추가한다.
- 중복 CSV는 제거한다.
- 유효 관측치가 2개 미만이면 GitHub에 포함된 22-row 기준 관측 CSV를 fallback으로 추가한다.

출력:

- `configs/generated/b2_bo_recommendations_{run_id}.csv`
- `results/metrics/parameter_input_sim_bo/{run_id}/bo_observations.csv`
- `results/metrics/parameter_input_sim_bo/{run_id}/bo_excluded_observations.csv`
- `results/metrics/parameter_input_sim_bo/{run_id}/bo_recommendations.csv`
- `results/metrics/parameter_input_sim_bo/{run_id}/bo_commands.sh`

다음 단계에서 쓸 파일:

- `configs/generated/b2_bo_recommendations_{run_id}.csv`
- 자동 생성된 `bo_commands.sh`에 실제 경로가 들어간다.

## Step 4. 추천 theta 실행

Step 3의 `bo_commands.sh` 첫 번째 시뮬레이션 명령을 실행한다. 기본 정책은 추천 theta 5개를 seed 5회 실행하는 것이다.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B2 \
  --b2-params configs/generated/b2_bo_recommendations_{run_id}.csv \
  --repeats 5 \
  --workers 1 \
  --emergency-depart 600 \
  --timeout-steps 7200 \
  --recovery-buffer-sec 300
```

입력:

- 추천 theta CSV

출력:

- `results/metrics/parameter_input_sim/{sim_run_id}/experiment_results.csv`
- `results/metrics/parameter_input_sim/latest.json`

## Step 5. Step 3-4 반복

권장 반복:

- 4라운드
- 라운드당 추천 theta 5개
- 각 theta seed 5회

반복할 때는 다시 자동 추천 명령만 실행한다.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage suggest \
  --bo-auto-inputs \
  --bo-recommend-count 5
```

주의:

- `latest.json`은 마지막 실행만 가리킨다.
- 여러 prefix를 섞어 실행했다면 `--bo-initial-results path1 path2 ...`로 직접 누적 CSV를 지정하는 편이 더 안전하다.

## Step 6. Top3 재평가 CSV 생성

명령어:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage top3 \
  --bo-auto-inputs
```

입력:

- 자동으로 누적된 관측 CSV

출력:

- `configs/generated/b2_bo_top3_reeval_{run_id}.csv`
- `results/metrics/parameter_input_sim_bo/{run_id}/bo_commands.sh`

선정 기준:

- 같은 theta가 여러 seed로 실행됐으면 `bo_score_sec` 평균을 쓴다.
- `bo_score_sec`가 없으면 `score_sec`를 fallback으로 쓴다.
- 실패, 미도착, teleport, route error row는 후보에서 제외한다.

## Step 7. Top3 재평가와 최종 비교

Top3 재평가:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B2 \
  --b2-params configs/generated/b2_bo_top3_reeval_{run_id}.csv \
  --repeats 5 \
  --workers 1 \
  --emergency-depart 600 \
  --timeout-steps 7200 \
  --recovery-buffer-sec 300
```

최종 B0/B2 비교:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B0 B2 \
  --b2-params configs/generated/b2_bo_top3_reeval_{run_id}.csv \
  --repeats 5 \
  --workers 1 \
  --emergency-depart 600 \
  --timeout-steps 7200 \
  --recovery-buffer-sec 300 \
  --output-prefix parameter_input_sim_final_compare
```

최종 비교는 최소 seed 3회, 가능하면 seed 5-10회를 권장한다.

## 기존 22-row 관측 CSV로 시작하기

GitHub에는 smoke와 초기 추천용 22-row candidate summary가 들어 있다.

```text
results/metrics/parameter_input_sim/initial_observations/parameter_input_sim_candidate_summary.csv
```

이 파일로 바로 추천하려면:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage suggest \
  --bo-initial-results results/metrics/parameter_input_sim/initial_observations/parameter_input_sim_candidate_summary.csv \
  --bo-recommend-count 5
```

기존 호환 alias도 동작한다.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bayesian true \
  --bo-initial-results results/metrics/parameter_input_sim/initial_observations/parameter_input_sim_candidate_summary.csv \
  --bo-recommend-count 5
```

## 수동 run id 찾기

자동 입력이 실패하거나 다른 컴퓨터에서 `state.json`이 없으면 수동으로 경로를 확인한다.

```bash
ls results/metrics/parameter_input_sim_bo
cat results/metrics/parameter_input_sim_bo/latest.json
cat results/metrics/parameter_input_sim_bo/state.json
cat results/metrics/parameter_input_sim/latest.json
```

`latest.json`의 `results_csv` 값을 직접 넘긴다.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage suggest \
  --bo-initial-results \
    results/metrics/parameter_input_sim/{run_id_1}/experiment_results.csv \
    results/metrics/parameter_input_sim/{run_id_2}/experiment_results.csv \
  --bo-recommend-count 5
```

## 한계

- GitHub에 올라간 결과가 로컬 최신 결과와 다를 수 있다.
- `state.json`은 로컬 workflow 상태라 다른 컴퓨터에서는 없거나 오래됐을 수 있다.
- `latest.json`은 마지막 실행 하나만 가리킨다.
- 여러 실험 prefix를 섞으면 자동 누적이 잘못된 CSV를 읽을 수 있다.
- 자동 모드는 같은 route/pipeline/mode 기준 결과를 쓰도록 설계했지만, 의심스러우면 `--bo-initial-results`로 직접 지정한다.

## Alpha 진단

`alpha`는 계속 최적화 변수로 둔다. 다만 현재 신호 로직에서는 이미 green extension이 충분히 크면 `alpha`가 결과를 바꾸지 않을 수 있다.

진단 컬럼:

- `alpha_hold_count`
- `alpha_effective_extension_sec`
- `total_extension_delta_sec`
- `signal_burden_penalty_sec`

`alpha_effective_extension_sec=0`이 반복되면 현재 구조에서는 `alpha` 민감도가 낮다고 해석한다.

## Smoke Test

SUMO를 실행하지 않고 BO CSV 생성만 확인:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage init \
  --bo-initial-count 5 \
  --bo-output-prefix parameter_input_sim_bo_smoke \
  --bo-workflow-prefix parameter_input_sim_bo_smoke
```

기존 22-row 관측으로 추천만 확인:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bo-stage suggest \
  --bo-initial-results results/metrics/parameter_input_sim/initial_observations/parameter_input_sim_candidate_summary.csv \
  --bo-recommend-count 3 \
  --bo-output-prefix parameter_input_sim_bo_smoke \
  --bo-workflow-prefix parameter_input_sim_bo_smoke
```
