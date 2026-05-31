# Bayesian Optimization 실행 안내

이 문서는 B2 신호 제어 파라미터 `D_det`, `alpha`, `G_ext`를 기존 실험 결과로부터 추가 추천하는 절차를 설명한다. 새 LHS/Sobol initial design은 만들지 않고, 이미 실행한 B2 결과 CSV를 Bayesian Optimization의 초기 관측값으로 사용한다.

## 1. 목적

- 기존 B2 결과 CSV의 `mode=B2` row를 GP surrogate model의 학습 데이터로 사용한다.
- 기존 결과 중 `score_sec`가 가장 낮은 theta를 current best로 둔다.
- acquisition function으로 추가 theta 10~15개를 추천한다.
- 추천 theta는 바로 실행 가능한 B2 parameter CSV와 shell command로 출력한다.
- 추가 실행 후 전체 누적 결과에서 상위 3개 theta를 seed 3회 재평가한다.
- 최종 B0/B2 비교는 최소 seed 3회, 가능하면 seed 5~10회로 실행한다.

## 2. 입력 CSV

`--bo-initial-results`에는 기존 40개 결과 CSV를 넘긴다. 여러 CSV를 한 번에 넘길 수 있다.

허용 파일:

- `experiment_results.csv`
- `score_components.csv`
- 같은 필수 컬럼을 가진 candidate summary CSV

필수 컬럼:

```csv
D_det,alpha,G_ext,A_delay_sec,N_delay_sec,T_recovery_sec,score_sec
```

`score_sec` 대신 `Score` 컬럼만 있으면 내부에서 `score_sec`로 읽는다. `T_change_sec`는 BO 최적화 대상이 아니며 추천 CSV에는 항상 `10`으로 고정해 쓴다.

다음 row는 GP 학습에서 제외하고 `bo_excluded_observations.csv`에 제외 사유를 남긴다.

- `final_status=FAIL`
- `emergency_arrived=False`
- `emergency_teleport=True`
- `route_error_count>0`
- `sumo_exit_code!=0`
- 필수 수치 컬럼 누락 또는 숫자 변환 실패

## 3. BO 추천 실행

`{run_id}`는 실험 실행 시 자동으로 생성된 결과 폴더명이다. 직접 `run1`처럼 정하는 값이 아니며, 먼저 아래 명령으로 실제 폴더명을 확인한다.

```bash
ls results/metrics/parameter_input_sim
```

예시:

```text
20260531T034533_745237Z0000
latest.json
```

가장 최근 run의 결과 CSV는 `latest.json`에서 바로 확인할 수 있다.

```bash
cat results/metrics/parameter_input_sim/latest.json
```

출력의 `results_csv` 값을 `--bo-initial-results`에 넣는다.

BO 초기 관측으로는 B2 후보가 충분히 들어 있는 run을 골라야 한다. smoke처럼 B2 row가 1개뿐인 파일은 BO 품질이 낮거나 학습 데이터가 부족하다. CSV 안의 B2 row 개수는 아래처럼 확인한다.

```bash
python - <<'PY'
import csv
path = "results/metrics/parameter_input_sim/{run_id}/experiment_results.csv"
with open(path, newline="", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))
print(sum(1 for row in rows if row.get("mode") == "B2"))
PY
```

기본 실행:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bayesian true \
  --bo-initial-results results/metrics/parameter_input_sim/{run_id}/experiment_results.csv \
  --bo-recommend-count 15
```

여러 결과 CSV를 누적해서 학습할 때:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bayesian true \
  --bo-initial-results \
    results/metrics/parameter_input_sim/{initial_run_id}/experiment_results.csv \
    results/metrics/parameter_input_sim/{bo_run_id}/experiment_results.csv \
  --bo-recommend-count 15
```

재현성을 고정할 때:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bayesian true \
  --bo-initial-results path/to/existing_40_results.csv \
  --bo-recommend-count 15 \
  --bo-output-prefix parameter_input_sim_bo \
  --bo-seed 20260531
```

## 4. 모델과 추천 기준

- 모델: `sklearn.gaussian_process.GaussianProcessRegressor`
- kernel: `ConstantKernel * Matern(nu=2.5) + WhiteKernel`
- target: `score_sec`
- acquisition: minimization용 Expected Improvement
- exploration: `xi=0.05`
- 탐색 범위: 유효 기존 관측치의 `D_det`, `alpha`, `G_ext` min/max
- 후보 격자:
  - `D_det`: 50m 단위
  - `alpha`: 1초 단위
  - `G_ext`: 5초 단위
- 이미 실행된 theta와 같은 `D_det,alpha,G_ext` 조합은 추천에서 제외한다.

## 5. 출력 파일

BO 추천 모드는 SUMO를 실행하지 않는다. 추천 결과와 실행 명령만 만든다.

```text
results/metrics/{bo_output_prefix}/{bo_run_id}/
  bo_observations.csv
  bo_excluded_observations.csv
  bo_recommendations.csv
  bo_commands.sh
  bo_summary.json
```

추가로 기존 러너가 바로 읽을 수 있는 B2 parameter CSV를 만든다.

```text
configs/generated/b2_bo_recommendations_{bo_run_id}.csv
configs/generated/b2_bo_top3_reeval_{bo_run_id}.csv
```

`configs/generated/*.csv`와 `results/metrics/*/`는 생성 산출물이므로 GitHub에 올리지 않는다.

## 6. 추천 theta 실행

`bo_commands.sh`에는 3개 명령이 들어간다.

1. 추가 추천 theta를 seed 1회 실행

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B2 \
  --b2-params configs/generated/b2_bo_recommendations_{bo_run_id}.csv \
  --repeats 1 \
  --workers 1 \
  --emergency-depart 600 \
  --timeout-steps 7200 \
  --recovery-buffer-sec 300
```

`B00`을 함께 실행하는 이유는 `A_delay_sec`와 `score_sec` 계산에 같은 run의 자유류 기준이 필요하기 때문이다.

2. 누적 결과에서 상위 3개 theta를 seed 3회 재평가

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B2 \
  --b2-params configs/generated/b2_bo_top3_reeval_{bo_run_id}.csv \
  --repeats 3 \
  --workers 1 \
  --emergency-depart 600 \
  --timeout-steps 7200 \
  --recovery-buffer-sec 300
```

3. 최종 B0/B2 비교

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B0 B2 \
  --b2-params configs/generated/b2_bo_top3_reeval_{bo_run_id}.csv \
  --repeats 3 \
  --workers 1 \
  --emergency-depart 600 \
  --timeout-steps 7200 \
  --recovery-buffer-sec 300 \
  --output-prefix parameter_input_sim_final_compare
```

최종 비교는 최소 seed 3회로 실행하고, 시간이 허용되면 `--repeats 5`에서 `--repeats 10`까지 늘린다.

## 7. 해석

- `bo_summary.json`의 `current_best`는 기존 관측 중 `score_sec`가 가장 낮은 theta다.
- `bo_recommendations.csv`의 `posterior_mean`은 GP가 예측한 score 평균이다.
- `posterior_std`가 클수록 불확실성이 크고 exploration 성격이 강하다.
- `acquisition`이 클수록 다음 실행 후보로 우선순위가 높다.
- 추천 결과는 확정 최적값이 아니라 추가 실행 후보이므로, 반드시 seed 1회 실행 후 누적 결과로 다시 BO를 돌리거나 상위 3개 재평가로 검증한다.

## 8. Smoke Test

BO 추천 모드만 빠르게 확인할 때:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --bayesian true \
  --bo-initial-results local_archive/20260531_cleanup/tracked/results/metrics/parameter_input_sim_candidate_summary.csv \
  --bo-recommend-count 10 \
  --bo-output-prefix parameter_input_sim_bo_smoke \
  --bo-seed 20260531
```

이 smoke는 SUMO를 실행하지 않고, 기존 CSV 읽기, GP 학습, 추천 CSV/명령어 생성만 검증한다.
