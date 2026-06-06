# Compact V9 Corridor Baseline

이 폴더는 기존 expanded V7/B3 맵이 실험 목적보다 과도하게 커진 문제를 해결하기 위해 만든 새 맵 파이프라인입니다.

목표는 서울역과 중부소방서를 두 초점으로 하는 최소 타원형 corridor 맵을 만들고, 사용자가 HTML로 맵을 accept한 뒤 수요와 B0/B3 실험으로 넘어가는 것입니다.

## 현재 구현 범위

- 서울역/소방서를 초점으로 하는 타원형 분석 영역 생성
- 기존 expanded OSM 원본을 재사용하되 최종 SUMO net은 타원 polygon 내부 edge만 유지
- 퇴계로 S1-S22 현실 CSV 매핑
- `1 edge = 1 차선수` 방식의 메인도로 차선 복구
- 퇴계로 메인도로 1차선 금지
- 전역 `3→1` 차로 급감 금지 audit
- 소방서 진입부 virtual entry TLS 후보 생성
- 소방차 서울역 전방 route 생성
- HTML 리뷰 및 accept gate 준비

기준 현실 CSV:

`/Users/junlee/Desktop/js/toegye_ro_mainstream_segments_english.csv`

## 실행

```bash
.venv/bin/python "09 Compact Corridor Baseline/step01_build_compact_map_review.py"
```

리뷰 HTML:

`/Users/junlee/Desktop/js/results/html/compact_v9_map_review.html`

맵을 accept한 뒤에는 다음 JSON을 생성해 후속 수요/B0 단계에서 gate로 사용합니다.

`/Users/junlee/Desktop/js/data_prepared/compact_v9/acceptance/compact_v9_map_acceptance.json`

## B04 Baseline 실행

B04는 Compact V9 도로망에서 신호/수요 baseline을 만들고, B4가 사용할 B0 측정 proxy를 생성하는 단계입니다. 전체를 한 번에 재생성할 때는 다음 명령을 사용합니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-all
```

단계별 실행이 필요하면 아래 순서로 실행합니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-adopt-green18
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-map-segments
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-target-profile
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-build-demand --candidates B04_ad_variance_smoothed
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-run-b0 --candidates B04_ad_variance_smoothed
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-validate --candidates B04_ad_variance_smoothed
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-queue-audit --candidate B04_ad_variance_smoothed
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-traffic-demand-review --candidate B04_ad_variance_smoothed
.venv/bin/python "09 Compact Corridor Baseline/b04_baseline_pipeline.py" b04-review
```

### B04 명령 의미

| 명령 | 의미 | 주요 산출물 |
| --- | --- | --- |
| `b04-adopt-green18` | B04 기준 신호 net을 채택합니다. | `data_prepared/compact_v9/net/...B04_green18.net.xml` |
| `b04-map-segments` | 퇴계로 S1-S22 현실 CSV와 SUMO edge를 매핑합니다. | segment-edge mapping CSV |
| `b04-target-profile` | 현실 속도/통행시간/수요 target profile을 만듭니다. | target profile CSV |
| `b04-build-demand` | B04 후보 수요 route 파일을 생성합니다. | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_*.rou.xml` |
| `b04-run-b0` | 신호 제어 없는 B04/B0 SUMO run을 실행합니다. | `results/metrics/compact_v9_B04/{candidate}/` |
| `b04-validate` | B04 후보의 속도, 수요, queue proxy 재현성을 검증합니다. | candidate validation JSON/CSV |
| `b04-queue-audit` | B4 Stage1이 읽을 queue proxy와 measurement diagnostic을 생성합니다. | `results/metrics/compact_v9_B04/queue_audit/` |
| `b04-traffic-demand-review` | 수요/교통량 진단 리뷰를 생성합니다. | traffic demand review JSON |
| `b04-review` | 사람이 볼 HTML 리뷰를 생성합니다. | `results/html/` |
| `b04-all` | 위 B04 준비 절차를 묶어서 실행합니다. | 전체 B04 산출물 |

### B04 옵션 의미

| 옵션 | 적용 명령 | 의미 |
| --- | --- | --- |
| `--candidates` | `b04-build-demand`, `b04-run-b0`, `b04-validate` | 쉼표로 구분한 B04 후보명만 실행합니다. 생략하면 코드의 기본 후보 목록을 사용합니다. |
| `--candidate` | `b04-queue-audit`, `b04-traffic-demand-review` | manifest 선택값 대신 특정 후보를 진단용으로 읽습니다. audit/review 입력만 바꾸며 manifest를 갱신하지 않습니다. |

## B4 Stage1 실행

B4 Stage1은 B04 no-control 산출물을 읽어 B4 런타임이 사용할 정적 입력을 만듭니다. SUMO를 새로 돌리지 않고, 이미 생성된 B04 edgeData/laneData/tripinfo proxy를 읽습니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/b4_stage1_pipeline.py" b4-stage1
```

주요 산출물은 `data_prepared/compact_v9/b4_stage1/` 아래에 생성됩니다.

| 산출물 | 의미 |
| --- | --- |
| `b4_runtime_index.json` | B4 런타임이 읽는 교차로/현시/queue/event schema index |
| `b4_route_movement_plan.json` | EV route와 제어 대상 movement 순서 |
| `b4_approach_storage_link_plan.csv` | 각 movement의 접근부 edge/lane/storage 정의 |
| `b4_b0_measured_signal_params.csv` | B04 B0 edge/lane data에서 추정한 q/tQ/lambda proxy |
| `b4_stage2_b0_merge_hold_params.json` | Stage2 합류부 hold 계산에 쓰는 B0 proxy와 runtime fallback 정책 |
| `b4_case_b_candidates.csv` | Case B 병목 후보와 upstream/downstream 매핑 |
| `b4_stage1_summary.json` | Stage1 provenance, validation, decision-variable screening 요약 |

## B04/B4 Runtime 실행

B04 no-control과 B4 제어 run을 비교할 때는 `run_b0_b4_signal_pipeline.py`를 사용합니다. 기본 실행은 B004 자유류 기준, B04 no-control, B4 제어를 모두 실행합니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/run_b0_b4_signal_pipeline.py" \
  --modes B004,B04,B4 \
  --run-id b04_b4_smoke_001
```

B04 baseline만 확인할 때는 다음처럼 실행합니다. `B0`은 `B04`의 alias입니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/run_b0_b4_signal_pipeline.py" \
  --modes B04 \
  --run-id b04_only_001
```

B4만 실행할 때는 B04와 같은 net/demand에서 생성된 Stage1 디렉터리를 함께 넘기는 것이 원칙입니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/run_b0_b4_signal_pipeline.py" \
  --modes B4 \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml" \
  --stage1-dir "data_prepared/compact_v9/b4_stage1" \
  --hard-max-sim-time 4000 \
  --run-id b4_only_001
```

### Runtime 공통 옵션

| 옵션 | 의미 |
| --- | --- |
| `--modes` | 실행할 모드 목록입니다. `B004`는 배경수요 없는 자유류 EV 기준, `B04`는 no-control baseline, `B4`는 우선신호 제어 run입니다. |
| `--phase` | 런타임 설정 묶음입니다. 현재 기본값은 `bo-smoke`입니다. |
| `--run-id` | 결과 디렉터리 이름에 들어가는 실행 ID입니다. 생략하면 timestamp 기반 ID를 생성합니다. |
| `--run-root` | SUMO 원본 실행 파일, config, tripinfo, edgeData, laneData 저장 위치입니다. |
| `--metrics-root` | 요약 CSV/JSON 결과 저장 위치입니다. |
| `--net-file` | 실행할 SUMO `.net.xml`입니다. B04/B4 비교에서는 같은 net을 써야 합니다. |
| `--background-route` | B04/B4 배경 차량 route 파일입니다. B004에는 사용하지 않습니다. |
| `--stage1-dir` | B4가 읽을 Stage1 산출물 디렉터리입니다. `--net-file`, `--background-route`와 같은 provenance에서 만들어진 디렉터리를 써야 합니다. |
| `--hard-max-sim-time` | SUMO run 강제 종료 시간입니다. EV 미도착/정체 run을 제한할 때 사용합니다. |
| `--sumo-binary` | PATH의 기본 SUMO 대신 특정 SUMO 실행 파일을 지정합니다. |
| `--dry-run` | SUMO를 실행하지 않고 생성될 task/config 정보만 확인합니다. |
| `--emit-fcd` | FCD 출력을 추가로 씁니다. 시각화나 경로 애니메이션용이며 일반 반복 실험에서는 끄는 것이 기본입니다. |

### B4 제어인자 옵션

B4의 최적화 대상 결정변수는 5개입니다.

| 옵션 | 단위 | 의미 |
| --- | --- | --- |
| `--b4-alpha` | 무차원 | ETA buffer 계수입니다. `tE_eff = alpha * distance / v_E`로 EV 도착시간을 보수적으로 봅니다. |
| `--b4-t-lead` | 초 | Stage3 선행 점등 시점입니다. `TA <= t_lead`이면 목표 녹색 전환을 시도합니다. |
| `--b4-delta-t-thr` | 초 | 선점 트리거 게이트입니다. EV 유효 도착시간이 이 값보다 크면 해당 step에서는 선점을 건너뜁니다. |
| `--b4-g-ext` | 초 | EV 통과 이후 유지할 녹색 여유입니다. legacy alias는 `--b4-ext-max`입니다. |
| `--b4-q-trig` | m | Stage2 합류부 queue 개입 임계값입니다. 합류부 queue가 이 값 이상일 때 hold 개입을 허용합니다. |

아래 옵션은 현재 구현에서 민감도를 점검할 수 있는 고정 구조 파라미터입니다. 기본 실험에서는 결정변수가 아니라 구조값/진단값으로 취급합니다.

| 옵션 | 의미 |
| --- | --- |
| `--b4-tau` | Case B 병목 판정 임계입니다. queue fill이 tau 이상이면 downstream-first 병목 처리를 유도합니다. |
| `--b4-hold-max` | Stage3 hold budget의 고정 구조 부분입니다. |
| `--b4-d-up` | 한 step에서 upstream/downstream lookahead와 신규 action budget을 제한합니다. |
| `--b4-tau-scale` | original tau fill에 곱하는 scale입니다. |
| `--b4-tau-numerator-gamma` | original tau fill의 비선형 보정 지수입니다. |
| `--b4-parameter-id` | 결과에 남길 B4 파라미터 ID입니다. |
| `--b4-theta` | 호환성 flag입니다. 현재 B4ThetaParams가 기본 런타임입니다. |

## B4 BO 실행

5개 결정변수 `alpha`, `t_lead`, `delta_T_thr`, `G_ext`, `Q_trig`를 Bayesian Optimization으로 탐색할 때 사용합니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/run_b4_theta_bo.py" \
  --run-id b4_theta_bo_001 \
  --initial-count 20 \
  --bo-rounds 10 \
  --bo-batch-size 5 \
  --repeats 1 \
  --workers 4 \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml" \
  --stage1-dir "data_prepared/compact_v9/b4_stage1" \
  --hard-max-sim-time 4000
```

### BO 옵션 의미

| 옵션 | 의미 |
| --- | --- |
| `--output-prefix` | BO 결과를 저장할 metrics/run 하위 prefix입니다. |
| `--initial-count` | 초기 실험점 개수입니다. |
| `--bo-rounds` | BO 추천/실행/재학습 반복 라운드 수입니다. |
| `--bo-batch-size` | 라운드마다 새로 추천할 후보 수입니다. |
| `--repeats` | 후보 하나를 반복 실행할 횟수입니다. |
| `--seed` | BO와 SUMO 비교에 쓰는 난수 seed입니다. |
| `--workers` | 병렬 실행 worker 수입니다. |
| `--w-emv`, `--w1` | 목적함수에서 EV 통행시간에 주는 가중치입니다. |
| `--w-veh`, `--w2` | 목적함수에서 일반차 평균 통행시간/지체에 주는 가중치입니다. |
| `--ei-candidate-count` | Expected Improvement 후보 sampling 개수입니다. |
| `--spc-stop` | SPC 기반 조기종료 판단을 켭니다. |
| `--spc-window` | SPC 판단에 쓸 최근 라운드 window 크기입니다. |
| `--spc-alpha` | SPC 통계 유의수준입니다. |
| `--spc-min-rounds` | 조기종료 판단 전 최소 라운드 수입니다. |
| `--spc-min-improvement-sec` | 의미 있는 개선으로 볼 최소 score 개선폭입니다. |
| `--tau-scale`, `--tau-numerator-gamma` | BO 중 고정할 tau 보정 구조 파라미터입니다. 5개 BO 결정변수에는 포함하지 않습니다. |
| `--resume`, `--bo-resume` | 기존 `latest/state`를 읽어 중단된 BO를 이어갑니다. |
| `--mock-eval` | SUMO 대신 mock 평가를 사용합니다. 코드 경로 확인용입니다. |

## 민감도 분석 실행

5개 결정변수 OFAT 민감도는 다음 명령으로 실행합니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/run_b4_theta_ofat_sensitivity.py" \
  --run-id b4_theta_ofat_001 \
  --only-variable alpha \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml" \
  --stage1-dir "data_prepared/compact_v9/b4_stage1" \
  --hard-max-sim-time 4000
```

`--only-variable`에는 `alpha`, `t_lead`, `delta_T_thr`, `G_ext`, `Q_trig` 중 하나를 넣습니다. 빈 값이면 모든 결정변수 OFAT을 실행합니다.

고정 구조 파라미터 민감도는 다음 명령으로 실행합니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/run_b4_fixed_param_sensitivity.py" \
  --run-id b4_fixed_param_001 \
  --only-variable tau \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml" \
  --stage1-dir "data_prepared/compact_v9/b4_stage1" \
  --hard-max-sim-time 4000
```

고정 구조 민감도의 `--only-variable`에는 `tau`, `tau_scale`, `tau_numerator_gamma`, `hold_max`, `d_up` 중 하나를 넣습니다. `--require-target15-baseline`을 켜면 B04 no-control EV 정지포함 평균속도가 target 범위를 벗어날 때 실행을 실패로 처리합니다.

## Provenance 원칙

B4 결과를 비교할 때 `--net-file`, `--background-route`, `--stage1-dir`은 같은 B04 실행 산출물에서 나온 조합이어야 합니다. 서로 다른 B04 run에서 나온 net/demand/Stage1을 섞으면 Stage2 merge B0 값, Case B 후보, queue proxy가 현재 실행 조건을 설명하지 못할 수 있습니다.

결과 파일은 기본적으로 `results/metrics/...`에 요약 CSV/JSON으로, `runs/...`에 SUMO 원본 산출물로 저장됩니다.
