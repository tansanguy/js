# Compact V9 Corridor Baseline

이 폴더는 중부소방서-서울역 Compact V9 corridor의 B04 no-control baseline, B4 Stage1 artifact, B04/B4 runtime 실행을 관리합니다. 현재 최종 검증 정본은 S1-forced net과 `B04_ad_stage23_trigger` demand를 쓰는 `B04_B4_S1_FORCED_OPTIMIZATION` profile입니다.

## 최신 정본

| 항목 | 값 |
| --- | --- |
| net | `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml` |
| demand | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml` |
| B04/B0 metric source | `results/metrics/compact_v9_B04/B04_ad_stage23_trigger/` |
| Stage1 | `data_prepared/compact_v9/b4_stage1_s1forced` |
| Stage1 measurement source | `B04_ad_stage23_trigger` |
| active input manifest | `configs/compact_v9_B04_B4_active_inputs.json` |
| 최종 최적화 runner | `09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py` |
| 목적함수 | `Score = (10/11) * delay_A + (1/11) * delay_N` |

## 최신 문서

| 문서 | 역할 |
| --- | --- |
| `README.md` | 9번 계열 실행 매뉴얼입니다. |
| `B4_09_RUN_CONDITIONS_AUDIT_KO.md` | 최신 입력과 자동 감사 기준입니다. |
| `B4_FINAL_DECISION_VARIABLES_IMPLEMENTATION_AUDIT_KO.md` | `/Users/junlee/Desktop/js/1 4 최종_결정변수와 알고리즘 3773b21010b280f69473f5455be7ec01.md` 구현 적합도 감사입니다. |
| `B4_FINAL_DECISION_VARIABLES_REVIEW_TODO_KO.md` | 확인할 것, 부족한 것, 의심스러운 것 점검표입니다. |
| `B4_OPTIMIZATION_FLOW_CURRENT_KO.md` | 비교 실험, 단일 BO, Pareto 가중치 sweep 흐름 설명입니다. |

## 1. 환경 확인

```bash
cd /Users/junlee/Desktop/js
```

```bash
.venv/bin/python -m pip install -r requirements.txt
```

```bash
.venv/bin/python 00_setup/verify_env.py
```

## 2. 실행 조건 감사

```bash
.venv/bin/python "09-1 B4 Optimization S1forced/audit_09_run_conditions.py"
```

정상 기대값은 `FAIL=0`, `WARN=0`, `INFO=0`입니다.

## 3. B04 Baseline 실행

B04는 Compact V9 도로망에서 신호/수요 baseline을 만들고, B4 Stage1이 읽을 B0 측정 proxy를 생성하는 단계입니다. 최종 후보만 재생성할 때는 아래 순서로 실행합니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-build-demand --candidates B04_ad_stage23_trigger
```

```bash
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-run-b0 --candidates B04_ad_stage23_trigger --force
```

```bash
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-validate --candidates B04_ad_stage23_trigger
```

현재 확인된 validation 상태는 `WARN`입니다. `B04_ad_stage23_trigger`의 S1-forced canonical 신호망/수요 run은 `emergency_arrived=true`, `emergency_teleport=false`, `background_teleported=0`, `stage23_teleported=0`, `base_background_teleported=0`, `speed_sanity_fail_count=0`, `metric_invalid_count=0`, `free_count=0`, `speed_mae_kmh=4.773`, `travel_time_mae_s=23.35`, `queue_top10_overlap=4`입니다. `queue_top10_overlap`은 병목 위치 정합성 진단값으로 유지하지만 validation 실패 조건으로 사용하지 않습니다.

09-1 real smoke는 S1-forced net으로 B04 baseline을 다시 돌립니다. S1-forced net의 EV route uncontrolled minor connection 3개를 priority `M`으로 보정하고, `347237859#0`을 지나는 기존 비-Stage23 배경 수요를 제거해 Stage23 trigger만 남기면서 baseline gate를 통과했습니다. split 재배치 probe는 09-1 평가에 FAIL이 섞여 canonical에서 제외했습니다. smoke baseline은 `final_status=PASS`, `termination_reason=ev_arrived_min_summary`, `T_actual_EMV_sec=451.0`, `emergency_arrived=true`, `emergency_teleport=false`, `background_teleported=0`입니다.

Stage23 삽입량, 자연 route, 도착시각 역산 time-window grid를 재탐색하려면 다음 명령을 사용합니다. 기본 grid는 720개 후보이며, 개발 확인에는 `--max-candidates`를 지정할 수 있습니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-calibrate-stage23 --candidates-grid default
```

2026-06-07 기준으로 최종 canonical 수요는 `results/metrics/compact_v9_B04/B04_ad_stage23_trigger/B04_segment_speed_recall.csv`에서 확인합니다. 추가 수요 보강 실험은 `queue_top10_overlap`을 올리기 전에 다른 S구간의 속도/정체 균형을 깨는 경향이 있어 canonical에서 제외했고, overlap 수치는 preflight gate가 아닌 진단 지표로 둡니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-queue-audit --candidate B04_ad_stage23_trigger
```

```bash
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-traffic-demand-review --candidate B04_ad_stage23_trigger
```

전체 B04 준비 절차를 한 번에 재생성하려면 다음 명령을 사용합니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-all
```

### B04 명령 의미

| 명령 | 의미 | 주요 산출물 |
| --- | --- | --- |
| `b04-build-demand` | B04 후보 수요 route 파일을 생성합니다. | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_*.rou.xml` |
| `b04-run-b0` | 신호 제어 없는 B04/B0 SUMO run을 실행합니다. | `results/metrics/compact_v9_B04/{candidate}/` |
| `b04-validate` | B04 후보의 속도, 수요, queue proxy 재현성을 검증합니다. | candidate validation JSON/CSV |
| `b04-queue-audit` | B4 Stage1이 읽을 queue proxy와 measurement diagnostic을 생성합니다. | `results/metrics/compact_v9_B04/queue_audit/` |
| `b04-traffic-demand-review` | 수요/교통량 진단 리뷰를 생성합니다. | traffic demand review JSON |
| `b04-review` | 사람이 볼 HTML 리뷰를 생성합니다. | `results/html/` |
| `b04-all` | B04 준비 절차를 묶어서 실행합니다. | 전체 B04 산출물 |

### B04 옵션 의미

| 옵션 | 적용 명령 | 의미 |
| --- | --- | --- |
| `--candidates` | `b04-build-demand`, `b04-run-b0`, `b04-validate` | 쉼표로 구분한 B04 후보명만 실행합니다. |
| `--candidate` | `b04-queue-audit`, `b04-traffic-demand-review` | 특정 후보를 진단용으로 읽습니다. |

## 4. B4 Stage1 실행

B4 Stage1은 B04 no-control 산출물을 읽어 B4 런타임이 사용할 정적 입력을 만듭니다. 최신 Stage1의 primary candidate와 B0 measurement source는 모두 `B04_ad_stage23_trigger`입니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/b4_stage1_pipeline.py" b4-stage1 \
  --stage1-dir "data_prepared/compact_v9/b4_stage1_s1forced" \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml"
```

주요 산출물:

| 산출물 | 의미 |
| --- | --- |
| `b4_runtime_index.json` | B4 런타임이 읽는 교차로/현시/queue/event schema index |
| `b4_route_movement_plan.json` | EV route와 제어 대상 movement 순서 |
| `b4_approach_storage_link_plan.csv` | 각 movement의 접근부 edge/lane/storage 정의 |
| `b4_b0_measured_signal_params.csv` | `B04_ad_stage23_trigger` B0 edge/lane data에서 추정한 q/tQ/lambda proxy |
| `b4_stage2_b0_merge_hold_params.json` | Stage2 합류부 hold 계산에 쓰는 B0 proxy와 runtime fallback 정책 |
| `b4_case_b_candidates.csv` | Case B 병목 후보와 upstream/downstream 매핑 |
| `b4_stage1_summary.json` | Stage1 provenance, validation, decision-variable screening 요약 |

## 5. B04/B4 Runtime 실행

B04 no-control과 B4 제어 run을 비교할 때는 `run_b0_b4_signal_pipeline.py`를 사용합니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/run_b0_b4_signal_pipeline.py" \
  --modes B004,B04,B4 \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml" \
  --stage1-dir "data_prepared/compact_v9/b4_stage1_s1forced" \
  --hard-max-sim-time 4000 \
  --run-id b04_b4_smoke_001
```

B4만 실행할 때도 같은 net/demand/Stage1 묶음을 명시합니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/run_b0_b4_signal_pipeline.py" \
  --modes B4 \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml" \
  --stage1-dir "data_prepared/compact_v9/b4_stage1_s1forced" \
  --hard-max-sim-time 4000 \
  --run-id b4_only_001
```

### Runtime 옵션

| 옵션 | 의미 |
| --- | --- |
| `--modes` | `B004`, `B04`, `B4` 중 실행할 모드 목록입니다. |
| `--run-id` | 결과 디렉터리 이름에 들어가는 실행 ID입니다. |
| `--run-root` | SUMO 원본 실행 파일, config, tripinfo, edgeData, laneData 저장 위치입니다. |
| `--metrics-root` | 요약 CSV/JSON 결과 저장 위치입니다. |
| `--net-file` | 실행할 SUMO `.net.xml`입니다. B04/B4 비교에서는 같은 net을 써야 합니다. |
| `--background-route` | B04/B4 배경 차량 route 파일입니다. |
| `--stage1-dir` | B4가 읽을 Stage1 산출물 디렉터리입니다. |
| `--hard-max-sim-time` | SUMO run 강제 종료 시간입니다. |
| `--sumo-binary` | PATH의 기본 SUMO 대신 특정 SUMO 실행 파일을 지정합니다. |
| `--dry-run` | SUMO를 실행하지 않고 task/config 정보만 확인합니다. |
| `--emit-fcd` | FCD 출력을 추가로 씁니다. |

## 6. B4 결정변수

현재 최적화 대상 결정변수는 5개입니다.

| 변수 | 단위 | Stage | 코드/런타임 역할 |
| --- | --- | --- | --- |
| `t_lead` | 초 | Stage3 | `TA <= t_lead`이면 목표 녹색 전환을 시도합니다. |
| `delta_T_thr` | 초 | Stage3 | `tE_gate_target > delta_T_thr`이면 아직 멀다고 보고 이번 step 선점을 건너뜁니다. |
| `G_ext` | 초 | Stage3 | EV 통과 후 녹색을 얼마나 더 유지할지 정합니다. |
| `Q_ratio` | 무차원 [0, 1] | Stage1/2 | `Q_th = Q_ratio * L`입니다. |
| `tau` | 무차원 [0.70, 0.90] | Stage3 | `Lq >= tau * L` 계열 Case B spillback 판정에 쓰입니다. |

`hold_max`, `d_up`은 runtime 안전/구조 파라미터입니다. 최종 최적화 표의 결정변수로 쓰지 않습니다. 입력 호환 때문에 일부 alias가 코드에 남아 있어도 새 표와 그림의 X는 위 5개로 고정합니다.

## 7. 단일 BO 실행

단일 BO만 직접 돌릴 때는 `run_b4_theta_bo.py`를 사용할 수 있습니다. 최종 논문용 fixed-budget 비교 정본은 `09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py`입니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/run_b4_theta_bo.py" \
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

### BO 옵션

| 옵션 | 의미 |
| --- | --- |
| `--initial-count` | 초기 실험점 개수입니다. |
| `--bo-rounds` | BO 추천/실행/재학습 반복 라운드 수입니다. |
| `--bo-batch-size` | 라운드마다 추천할 후보 수입니다. |
| `--repeats` | 후보 하나를 반복 실행할 횟수입니다. |
| `--seed` | BO와 SUMO 비교에 쓰는 난수 seed입니다. |
| `--workers` | 병렬 실행 worker 수입니다. |
| `--w-emv`, `--w1` | 목적함수에서 EV delay에 주는 가중치입니다. |
| `--w-veh`, `--w2` | 목적함수에서 일반차 delay에 주는 가중치입니다. |
| `--ei-candidate-count` | Expected Improvement 후보 sampling 개수입니다. |
| `--spc-stop` | SPC 기반 조기종료 판단을 켭니다. |
| `--spc-window` | SPC 판단에 쓸 최근 라운드 window 크기입니다. |
| `--spc-alpha` | SPC 통계 유의수준입니다. |
| `--spc-min-rounds` | 조기종료 판단 전 최소 라운드 수입니다. |
| `--spc-min-improvement-sec` | 의미 있는 개선으로 볼 최소 score 개선폭입니다. |
| `--resume`, `--bo-resume` | 기존 state를 읽어 중단된 BO를 이어갑니다. |
| `--mock-eval` | SUMO 대신 mock 평가를 사용합니다. |

## 8. S1-forced 최종 최적화

`09-1 B4 Optimization S1forced/` runner는 같은 S1-forced 입력 묶음으로 Random Search, 표준 `cma` 패키지 기반 CMA-ES, BO를 fixed-budget 방식으로 비교하고, 논문 표/그림에 쓸 CSV/PNG를 만듭니다.

```bash
.venv/bin/python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
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

주요 산출물:

| 산출물 | 의미 |
| --- | --- |
| `table1_best_so_far.csv` | `method, seed, R1...R50` 형식의 누적 최솟값입니다. |
| `table2_bo_surrogate.csv` | BO 그림 2용 long-form 표입니다. |
| `table3_pareto.csv` | `1:1`, `5:1`, `10:1`, `15:1`, `20:1` 가중치별 최적 후보입니다. |
| `figure1_best_so_far.png` | 알고리즘별 fixed-budget 비교 그림입니다. |
| `figure2_bo_surrogate.png` | BO surrogate 그림입니다. |
| `figure3_pareto.png` | Pareto/knee 후보 그림입니다. |
| `noise_check_5repeat.csv` | 실제 5회 noise check 결과입니다. |
| `experiment_summary.json` | 입력, 예산, seed, 산출물 manifest입니다. |

## 9. Pareto 가중치 Sweep

민감도 분석의 정본은 가중치를 바꿔가며 응급차 지연과 일반차 지연의 맞교환을 보여주는 Pareto sweep입니다. 이 결과는 가중치를 정하기 위한 자동 결론이 아니라 정책 결정자가 선택할 수 있는 후보 목록입니다.

| 가중치(w1:w2) | 최적 theta | delay_A | delay_N |
| --- | --- | --- | --- |
| 1:1 | `table3_pareto.csv` | 결과값 | 결과값 |
| 5:1 | `table3_pareto.csv` | 결과값 | 결과값 |
| 10:1 | `table3_pareto.csv` | 결과값 | 결과값 |
| 15:1 | `table3_pareto.csv` | 결과값 | 결과값 |
| 20:1 | `table3_pareto.csv` | 결과값 | 결과값 |

각 가중치에서 net, demand, Stage1, 사고 위치, 출동 조건은 모두 동일해야 합니다. 하나의 가중치에는 BO 탐색 1회를 수행하고, SPC 기반으로 개선 변동이 잦아드는 지점에서 중단할 수 있습니다. 값이 튀는 경우에만 반복 탐색을 추가합니다.

`figure3_pareto.png`의 붉은 점은 knee point 보조 표시입니다. 이 표시는 10:1이 정답이라는 뜻도, knee point를 반드시 채택해야 한다는 뜻도 아닙니다.

## 10. Provenance 원칙

B4 결과를 비교할 때 `--net-file`, `--background-route`, `--stage1-dir`은 같은 B04 실행 산출물에서 나온 조합이어야 합니다. 서로 다른 B04 run에서 나온 net/demand/Stage1을 섞으면 Stage2 merge B0 값, Case B 후보, queue proxy가 현재 실행 조건을 설명하지 못합니다.
