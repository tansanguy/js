# Emergency Signal SUMO Project 실행 매뉴얼

중부소방서-서울역 corridor에서 SUMO 기반 응급차 우선신호 제어를 실험하는 프로젝트입니다. 현재 최종 검증 기준은 `09 Compact Corridor Baseline`과 `09-1 B4 Optimization S1forced` 계열입니다.

## 0. 최신 기준

최신 9번 계열 profile은 `B04_B4_S1_FORCED_OPTIMIZATION`입니다.

| 항목 | 최신 정본 |
| --- | --- |
| net | `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml` |
| demand | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml` |
| Stage1 | `data_prepared/compact_v9/b4_stage1_s1forced` |
| Stage1 B0 measurement source | `B04_ad_stage23_trigger` |
| active manifest | `configs/compact_v9_B04_B4_active_inputs.json` |
| 최적화 runner | `09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py` |
| 결정변수 | `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau` |
| 기본 목적함수 | `Score = (10/11) * delay_A + (1/11) * delay_N` |

## 1. 환경 설정

프로젝트 루트로 이동합니다.

```bash
cd /Users/junlee/Desktop/js
```

가상환경을 만들고 requirements를 설치합니다.

```bash
bash 00_setup/setup_venv.sh
```

가상환경을 활성화합니다.

```bash
source .venv/bin/activate
```

환경을 확인합니다. `verify_env.py`는 SUMO Python binding뿐 아니라 최적화 표/그림 생성에 필요한 `numpy`, `sklearn`, `matplotlib`까지 검사합니다.

```bash
python 00_setup/verify_env.py
```

패키지가 빠져 있으면 requirements를 다시 설치합니다.

```bash
python -m pip install -r requirements.txt
```

## 2. 최신 문서

| 문서 | 용도 |
| --- | --- |
| `09 Compact Corridor Baseline/README.md` | 9번 계열 전체 실행 매뉴얼입니다. |
| `09 Compact Corridor Baseline/B4_09_RUN_CONDITIONS_AUDIT_KO.md` | 최신 입력과 자동 감사 기준입니다. |
| `09 Compact Corridor Baseline/B4_FINAL_DECISION_VARIABLES_IMPLEMENTATION_AUDIT_KO.md` | `1 4 최종_결정변수와 알고리즘...md`가 코드에 얼마나 구현됐는지 정리한 문서입니다. |
| `09 Compact Corridor Baseline/B4_FINAL_DECISION_VARIABLES_REVIEW_TODO_KO.md` | 확인할 것, 부족한 것, 의심스러운 것을 정리한 문서입니다. |
| `09 Compact Corridor Baseline/B4_OPTIMIZATION_FLOW_CURRENT_KO.md` | 비교 실험, 단일 BO, Pareto 가중치 sweep 흐름 설명입니다. |
| `09-1 B4 Optimization S1forced/README.md` | S1-forced fixed-budget 최적화 runner 전용 실행 요약입니다. |

## 2-1. 디렉터리 역할

| 경로 | 역할 |
| --- | --- |
| `config` | 초기 지도/수요 준비 단계 설정입니다. |
| `configs` | 최종 manifest와 실행 입력 고정 파일을 둡니다. |
| `configs/generated` | BO가 만든 임시 추천 CSV 산출물 위치입니다. |
| `data_prepared` | 재현 가능한 준비 데이터와 canonical 입력을 둡니다. |
| `runs` | SUMO 원본 로그와 반복 실행 산출물 위치입니다. |
| `results` | 집계 지표, 표, 그림, HTML 검증 산출물 위치입니다. |

요약: `config`: 초기 지도/수요 준비 단계, `configs`: 최종 manifest, `configs/generated`: BO가 만든 임시 추천 CSV 산출물, `runs`: SUMO 원본 로그.

`configs/generated`와 `runs` 아래 파일은 기본적으로 임시 산출물입니다. 최종 재현 입력으로 쓰려면 `configs` 또는 `data_prepared`의 명시된 canonical 경로로 승격하고, manifest에 출처를 기록합니다.

## 3. 실행 조건 감사

실제 실행 전에 9번 계열 입력과 기본값이 최신 profile과 맞는지 확인합니다.

```bash
python "09-1 B4 Optimization S1forced/audit_09_run_conditions.py"
```

JSON 전체 보고서가 필요하면:

```bash
python "09-1 B4 Optimization S1forced/audit_09_run_conditions.py" --json
```

정상 기대값은 `FAIL=0`, `WARN=0`, `INFO=0`입니다.

## 4. 테스트

핵심 계약 테스트를 실행합니다.

```bash
python -m pytest tests/test_b4_09_run_conditions_audit.py -q
```

```bash
python -m pytest tests/test_b4_theta_bo.py tests/test_b4_stage1_contract.py tests/test_b4_runtime_contract.py -q
```

S1-forced optimizer 산출물 계약 테스트입니다. CSV와 PNG 생성을 같이 확인합니다.

```bash
python -m pytest tests/test_b4_optimization_s1forced.py -q
```

## 5. Mock Smoke 실행

SUMO 없이 runner 계약과 산출물 schema를 빠르게 확인합니다.

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

주요 옵션:

| 옵션 | 의미 |
| --- | --- |
| `--mock-eval` | SUMO 대신 deterministic mock evaluator를 사용합니다. |
| `--run-id` | `09-1 B4 Optimization S1forced/outputs/{run_id}` 결과 폴더 이름입니다. |
| `--n` | 방법별 seed 개수입니다. |
| `--m` | seed 하나당 평가 round 수입니다. |
| `--bo-initial` | BO 초기 random observation 수입니다. |
| `--workers` | SUMO 평가 병렬 worker 수입니다. |
| `--ei-candidate-count` | BO Expected Improvement 후보 sampling 개수입니다. |

## 6. Real Smoke 실행

SUMO를 실제로 돌리는 작은 검증입니다. 전체 최적화 전에 반드시 먼저 실행합니다.

현재 확인 상태:

- `B04_ad_stage23_trigger` Stage1 provenance는 PASS입니다.
- `b04-validate --candidates B04_ad_stage23_trigger` 결과는 현재 `WARN`입니다. S1-forced canonical 신호망/수요 기준으로 `emergency_arrived=true`, `emergency_teleport=false`, `background_teleported=0`, `speed_sanity_fail_count=0`, `metric_invalid_count=0`, `speed_mae_kmh=4.773`, `travel_time_mae_s=23.35`, `queue_top10_overlap=4`입니다. `queue_top10_overlap`은 병목 위치 정합성 진단값이며 preflight 실패 조건으로 사용하지 않습니다.
- 09-1 real smoke는 S1-forced baseline gate를 통과합니다. 확인값은 `final_status=PASS`, `termination_reason=ev_arrived_min_summary`, `T_actual_EMV_sec=451.0`, `emergency_arrived=true`, `emergency_teleport=false`, `background_teleported=0`입니다.
- `s1forced_queue_overlap_relaxed_smoke`의 `n=1`, `m=4`, `--skip-pareto`, `--skip-noise-check` smoke는 baseline gate를 통과했고 `all_evaluations.csv`, `table1_best_so_far.csv`, `table2_bo_surrogate.csv`, `figure1`, `figure2`를 생성했습니다. 평가 12개 중 11개는 `PASS`, BO 초기 theta 1개는 `emergency_stuck`으로 `FAIL`입니다.

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

추가 옵션:

| 옵션 | 의미 |
| --- | --- |
| `--net-file` | B04/B4가 함께 사용할 SUMO network입니다. |
| `--background-route` | B04/B4 배경수요 route 파일입니다. |
| `--stage1-dir` | B4 런타임이 읽을 Stage1 artifact 디렉터리입니다. |
| `--hard-max-sim-time` | EV 미도착/정체 run을 강제로 제한하는 최대 시뮬레이션 시간입니다. |

## 7. 최종 Fixed-Budget 최적화

제출용 비교 실험은 Random Search, 표준 `cma` 패키지 기반 CMA-ES, BO를 같은 예산으로 비교합니다.

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

기본 산출물:

| 산출물 | 의미 |
| --- | --- |
| `all_evaluations.csv` | 모든 theta 평가 long-form 결과입니다. |
| `table1_best_so_far.csv` | 방법별/seed별 best-so-far 표입니다. |
| `table2_bo_surrogate.csv` | BO 관측값, surrogate mean/CI, acquisition 표입니다. |
| `table3_pareto.csv` | `1:1`, `5:1`, `10:1`, `15:1`, `20:1` 가중치별 최적 후보입니다. |
| `figure1_best_so_far.png` | fixed-budget 방법 비교 그림입니다. |
| `figure2_bo_surrogate.png` | BO surrogate trace 그림입니다. |
| `figure3_pareto.png` | Pareto/knee 후보 그림입니다. |
| `noise_check_5repeat.csv` | 실제 5회 반복 noise check입니다. |
| `experiment_summary.json` | 입력, 예산, seed, 산출물 manifest입니다. |

## 8. Pareto 가중치 Sweep

민감도 분석의 정본은 가중치별 Pareto 후보를 펼쳐 보여주는 것입니다. 목적은 가중치를 정하는 것이 아니라, 응급차 지연과 일반차 지연의 맞교환을 정책 결정자가 볼 수 있게 만드는 것입니다.

| 가중치(w1:w2) | 최적 theta | delay_A | delay_N |
| --- | --- | --- | --- |
| 1:1 | `table3_pareto.csv`의 해당 행 | 결과값 | 결과값 |
| 5:1 | `table3_pareto.csv`의 해당 행 | 결과값 | 결과값 |
| 10:1 | `table3_pareto.csv`의 해당 행 | 결과값 | 결과값 |
| 15:1 | `table3_pareto.csv`의 해당 행 | 결과값 | 결과값 |
| 20:1 | `table3_pareto.csv`의 해당 행 | 결과값 | 결과값 |

각 가중치에서는 같은 net, demand, Stage1, 사고/출동 조건을 고정하고 BO 탐색을 1회 수행합니다. 기본 Pareto sweep은 SPC 기반으로 개선 변동이 잦아드는 지점에서 조기 중단할 수 있습니다. 값이 불안정하다고 판단될 때만 같은 가중치의 반복 실행을 추가합니다. 실제 30회 반복을 수행하지 않았다면 30회 반복 결과라고 쓰지 않습니다.

`figure3_pareto.png`에서 붉은 점은 knee point 보조 표시입니다. 10:1이 정답이라는 뜻도 아니고, knee point 가중치를 반드시 채택해야 한다는 뜻도 아닙니다. 최종 결정은 정책 결정자의 몫입니다.

## 9. 단일 Bayesian Optimization

기존 단일 BO 흐름만 돌릴 때 사용합니다. 점수 계산은 지연 우선 정규화 score로 최신화되어 있으며, 방법론 비교 표/그림에는 `09-1` fixed-budget runner를 사용합니다.

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

## 10. 최종 목적지 검증

10번은 9번에서 선택한 theta를 잠그고, 실제 목적지 3곳으로 보내 B004/B04/B4 성능을 검증하는 최종 실험입니다. 10번 안에서는 BO, CMA-ES, Random Search를 다시 실행하지 않습니다.

3개 지점은 고정하지 않습니다. 먼저 18개 validated target edge를 최신 S1-forced 입력으로 1회 screening하고, 최신 net에서 target edge가 없거나 연결되지 않는 후보는 precheck 제외로 기록합니다. 이후 EV 도착 성공, emergency teleport 없음, B4 개선, 실제 Stage2/Stage3 개입이 확인된 후보 중 개선폭과 대표성이 좋은 3개를 고릅니다.

```bash
python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv" \
  --workers 6 \
  --run-id final_destination_validation_001
```

산출물은 `results/metrics/compact_v9_final_destination_validation/{run_id}/` 아래에 저장됩니다. 실제 선택된 3개 지점과 선택 이유는 `final/final_destination_validation_report.md`에서 확인합니다.
