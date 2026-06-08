# 9번 계열 B04/B4 실행 조건 통일 감사

작성 기준: 2026-06-07 현재 코드베이스

이 문서는 9번 계열 폴더의 최신 실행 조건을 하나로 고정하기 위한 작업 기록입니다.

대상 폴더:

- `09 Compact Corridor Baseline`
- `09-1 B4 Optimization S1forced`

## Canonical Profile

최신 실행 기준 profile 이름은 `B04_B4_S1_FORCED_OPTIMIZATION`입니다.

| 항목 | 최신 정본 |
| --- | --- |
| net | `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml` |
| demand | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml` |
| Stage1 | `data_prepared/compact_v9/b4_stage1_s1forced` |
| Stage1 measurement source | `B04_ad_stage23_trigger` |
| active input manifest | `configs/compact_v9_B04_B4_active_inputs.json` |
| signal profile | `09 Compact Corridor Baseline/tdata_signal/global_reality_signal_profiles.csv` |
| signal mapping | `09 Compact Corridor Baseline/tdata_signal/global_tls_a008_itst_mapping.csv` |
| 최적화 runner | `09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py` |

## 결정변수와 목적함수

최신 최적화 결정변수는 5개입니다.

| 변수 | 상태 |
| --- | --- |
| `t_lead` | 최적화 변수 |
| `delta_T_thr` | 최적화 변수 |
| `G_ext` | 최적화 변수 |
| `Q_ratio` | 최적화 변수 |
| `tau` | 최적화 변수 |

최적화 표와 그림의 기본 목적함수:

```text
Score = (10/11) * D_E + (1/11) * D_G
```

`D_E`는 응급차 자유류 대비 지연이고, `D_G`는 `V_G` 영향권 일반차 대당 평균 지연입니다. `V_G`는 본선 route edge와 본선 교차로 TLS의 SUMO `.net.xml` incoming edge로 자동 구성합니다. 기존 표기 `D_E_sec`, `D_G_sec`은 각각 `D_E`, `D_G`에 대응합니다.

낮을수록 좋은 값입니다.

## 현재 적용 상태

| 위치 | 최신 기준 적용 상태 | 비고 |
| --- | --- | --- |
| `configs/compact_v9_B04_B4_active_inputs.json` | 적용 | canonical profile, 입력 경로, 5개 결정변수, 10:1 score weight를 명시합니다. |
| `09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py` | 적용 | Random Search, CMA-ES, BO를 같은 S1-forced 입력과 `n=15`, `m=50`으로 비교합니다. |
| `09 Compact Corridor Baseline/b4_runtime.py` | 적용 | net, demand, Stage1, 5개 결정변수, runtime `W_E:W_G=10:1`을 사용합니다. |
| `09 Compact Corridor Baseline/run_b0_b4_signal_pipeline.py` | 적용 | 기본 입력은 runtime에서 가져오며, `objective_score`는 정규화된 `w_E * D_E + w_G * D_G`입니다. |
| `09 Compact Corridor Baseline/b4_stage1_pipeline.py` | 적용 | primary candidate와 B0 measurement source가 모두 `B04_ad_stage23_trigger`입니다. 출력 Stage1은 `b4_stage1_s1forced`입니다. |
| `09 Compact Corridor Baseline/run_b4_theta_bo.py` | 적용 | 5개 변수, S1-forced 입력, 지연 우선 정규화 score를 사용합니다. 방법론 비교 표/그림 정본은 `09-1` runner입니다. |
| `09 Compact Corridor Baseline/README.md` | 적용 | 최신 실행 순서와 Pareto sweep 해석으로 갱신했습니다. |

## 자동 감사 도구

9번 계열 조건이 최신 profile과 맞는지 확인하는 CLI입니다.

```bash
python "09-1 B4 Optimization S1forced/audit_09_run_conditions.py"
```

JSON 전체 보고서:

```bash
python "09-1 B4 Optimization S1forced/audit_09_run_conditions.py" --json
```

정상 기대값:

```text
FAIL=0
WARN=0
INFO=0
```

감사 정책:

- canonical manifest, runner default, runtime default가 틀리면 `FAIL`
- Stage1 primary candidate와 measurement source가 `B04_ad_stage23_trigger`가 아니면 `FAIL`
- S1-forced canonical net의 firetruck route uncontrolled connection이 minor/yield `m` 또는 `o` 상태이면 `FAIL`
- fixed-budget runner의 Pareto 표 필드가 `weight_ratio`, 5개 theta, `D_E_sec`, `D_G_sec`, SPC/knee 정보를 포함하지 않으면 `FAIL`
- 문서/과거 산출물 문자열 scan은 현재 정본 감사에서 제외합니다. 감사 기준은 실행 경로와 기본값입니다.

## Workers 6 실행 순서

```bash
cd /Users/junlee/Desktop/js
```

```bash
python3 -m venv .venv
```

```bash
. .venv/bin/activate
```

```bash
python -m pip install --upgrade pip
```

```bash
python -m pip install -r requirements.txt
```

```bash
python 00_setup/verify_env.py
```

```bash
python "09-1 B4 Optimization S1forced/audit_09_run_conditions.py"
```

```bash
python -m pytest tests/test_b4_09_run_conditions_audit.py tests/test_b4_optimization_s1forced.py -q
```

```bash
python -m pytest tests/test_b4_theta_bo.py tests/test_b4_stage1_contract.py tests/test_b4_runtime_contract.py -q
```

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

Full fixed-budget:

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

## 팀원에게 공유할 구현 설명

`/Users/junlee/Desktop/js/1 4 최종_결정변수와 알고리즘 3773b21010b280f69473f5455be7ec01.md` 기준으로 현재 코드는 S1-forced 입력 묶음, 5개 결정변수, fixed-budget `n=15`, `m=50`, Random Search/표준 `cma` 기반 CMA-ES/BO 비교, BO surrogate 표, Pareto 표와 PNG 산출까지 구현했습니다.

다만 full real SUMO `n=15, m=50` 결과가 이미 생성됐다고 쓰면 안 됩니다. 실제 결과는 full optimization 명령을 돌린 뒤 `outputs/{run_id}` 아래 CSV/PNG 존재와 row 수를 확인해야 완료입니다.

현재 S1-forced real smoke는 baseline gate를 통과합니다. `s1forced_queue_overlap_relaxed_smoke`의 `n=1`, `m=4`, `--skip-pareto`, `--skip-noise-check` 기준으로 기본 CSV/PNG가 생성됐고, 평가 12개 중 11개는 `PASS`, BO 초기 theta 1개는 `emergency_stuck`으로 `FAIL`입니다. B04 baseline recall은 canonical 신호망/수요 기준 `WARN`이며, hard metric은 통과했습니다. `queue_top10_overlap=4`는 병목 위치 정합성 진단값으로만 유지하고 preflight 실패 조건으로 사용하지 않습니다.

noise check는 실제 5회 반복 artifact만 문서화합니다. 30회 반복을 수행하지 않았다면 30회 반복했다고 쓰지 않습니다.
