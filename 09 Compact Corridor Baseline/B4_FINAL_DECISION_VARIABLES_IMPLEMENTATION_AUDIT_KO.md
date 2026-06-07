# B4 최종 결정변수/알고리즘 구현 적합도 감사

작성 기준: 2026-06-07 현재 코드베이스

참조 문서: `/Users/junlee/Desktop/js/1 4 최종_결정변수와 알고리즘 3773b21010b280f69473f5455be7ec01.md`

현재 구현은 위 문서의 큰 구조인 `Stage1 -> Stage2 -> Stage3`, 그리고 최종 결정변수 `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau`에 맞춰 정리되어 있습니다. 모든 수식과 현장 관측 조건이 100% 그대로 구현된 것은 아니므로, 아래에 맞는 부분과 남은 리스크를 구분합니다.

## 결론

- 최종 결정변수 5개는 코드에 반영되어 있습니다.
- 최신 입력은 S1-forced net, `B04_ad_stage23_trigger` demand, `b4_stage1_s1forced` Stage1로 통일되어 있습니다.
- Stage1 primary candidate와 B0 measurement source는 모두 `B04_ad_stage23_trigger`입니다.
- S1-forced 최적화 runner는 Random Search, 표준 `cma` 패키지 기반 CMA-ES, BO fixed-budget 비교와 Pareto 가중치 sweep을 구현합니다.
- full real SUMO 최적화 `n=15, m=50`은 실제 실행 산출물을 확인해야 완료라고 말할 수 있습니다.

## 결정변수 X 적용 상태

| 참조 문서 변수 | 코드 변수 | 적용 상태 | 근거 |
| --- | --- | --- | --- |
| `t_lead` | `t_lead` | 적용됨 | `B4_DECISION_VARIABLES`, `B4ThetaParams`, Stage3 TA trigger에서 사용 |
| `Delta T_th` | `delta_T_thr` | 적용됨 | `stage3_delta_gate_open()`에서 gate 기준으로 사용 |
| `G_ext` | `G_ext` | 적용됨 | `target_phase_duration()`, active control extension에서 사용 |
| `Q_ratio` | `Q_ratio` | 적용됨 | Stage2 `Q_th_merge = Q_ratio * L_merge_m`, Stage3 `q_th_m = Q_ratio * L_m` 기록 |
| `tau` | `tau` | 적용됨 | Case B 판정에서 `queue >= tau * L` 계열 로직으로 사용 |

코드상 최종 변수 목록:

- `09 Compact Corridor Baseline/b4_runtime.py`: `B4_DECISION_VARIABLES = ("t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau")`
- `09 Compact Corridor Baseline/run_b4_theta_bo.py`: `THETA_FIELDS`
- `09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py`: `THETA_FIELDS`

## Stage별 구현 대응

### Stage1 Initialize

참조 문서 요구:

- 출동 시 1회 경로, 교차로 번호, 합류 상수, `i_merge` 확정
- `Q_th = Q_ratio * L` 형태로 링크 길이 기반 queue 임계 설정
- `t_dispatch_delay = 45`, `tE_merge = 10` 타임라인 반영

현재 구현:

- `B4Stage1Inputs.load()`가 `b4_runtime_index.json`, `b4_departure_flow_plan.json`, `b4_stage2_b0_merge_hold_params.json`, `b4_route_movement_plan.json` 계열 Stage1 산출물을 읽습니다.
- S1-forced 정본 Stage1은 `data_prepared/compact_v9/b4_stage1_s1forced`입니다.
- Stage1 summary의 `primary_candidate`와 `measurement_source_candidate`는 모두 `B04_ad_stage23_trigger`입니다.
- `b4_stage2_b0_merge_hold_params.json`에는 `t_dispatch_delay_sec=45`, `tE_merge_sec=10`, `Q_th_merge_formula = Q_ratio * L_merge_m`이 들어 있습니다.

판정: 대체로 적용됨.

주의:

- 현재 SUMO 실험에서는 “출동 시 1회 계산” 결과가 Stage1 artifact로 사전 고정됩니다.
- Stage1 재생성 후 provenance summary가 PASS인지 확인해야 합니다.

### Stage2 합류구간 제어

참조 문서 요구:

- EV 출발 전 45초 대기 동안 합류부를 관찰
- `Lq_merge >= Q_ratio * L_merge`이고 `T_hold <= 0`이면 red-hold
- SafetyGate를 통과한 신호 명령만 실행

현재 구현:

- `handle_stage2()`는 `dispatch_detect_time = ev_depart_sec - t_dispatch_delay_sec` 이후부터 합류부를 봅니다.
- `should_start_stage2_hold()`에서 `q_th_merge = Q_ratio * L_merge_m`를 계산합니다.
- `T_hold_proxy_sec <= 0` 조건을 hold 시작 판단에 반영합니다.
- 실제 명령은 `apply_tls_request()`를 거쳐 적용됩니다.

판정: 핵심 조건은 적용됨.

주의:

- queue는 실제 현장 관측치가 아니라 SUMO TraCI snapshot/proxy입니다.
- Stage2 B0 값은 fallback/provenance 성격이고, runtime snapshot이 primary입니다.

### Stage3 Preemption

참조 문서 요구:

- EV 출발 후 매 1초 상태 수집
- ahead movement를 보고 Case A/B 판정
- Case B는 downstream-first
- `tE > delta_T_thr`이면 skip
- `TA <= t_lead`이면 녹색 전환
- `green_dur = max(Gm, tS + t_pass + G_ext)`
- SafetyGate 통과분만 실행

현재 구현:

- `handle_stage3()`는 EV 출발 이후에만 동작합니다.
- `stage3_delta_gate_open()`에서 `delta_T_thr` gate를 적용합니다.
- `theta_ta_lead_sec()`와 `TA_proxy_sec <= t_lead`로 선점 trigger를 판단합니다.
- `target_phase_duration()`은 `max(Gm, tS + t_pass + G_ext)`에 가까운 방식으로 duration을 계산합니다.
- Case B 관련 판정은 `case_b_tau()`, `stage3_case_plans()`, `runtime_tau_segment/runtime_tau_movement`, `case_b_downstream_first` 계열 로그로 구현되어 있습니다.
- SafetyGate는 `safety_gate()`와 `apply_tls_request()`에서 처리됩니다.

판정: 핵심 로직은 적용됨.

주의:

- Case B downstream-first가 모든 교차로에서 의도대로 발동했는지는 실제 event log 검증이 필요합니다.
- 최신 기준의 Case B 판정은 runtime queue/segment fill과 결정변수 `tau`를 사용합니다.

### SafetyGate

현재 구현은 `DENY_PEDESTRIAN_MIN_GREEN`, `DENY_CLEARANCE_INCOMPLETE`, `REQUIRE_CLEARANCE`, `DENY_CLEARANCE_UNAVAILABLE` 상태를 반환하고, clearance가 필요하면 target green을 즉시 덮어쓰지 않습니다.

판정: 코드 구조상 적용됨.

주의:

- 보행 현시는 실제 현장 보행 신호 데이터 완전 재현이 아니라 Stage1/설정 기반 최소녹색 proxy입니다.

## 목적함수 적용 상태

참조 문서 요구:

```text
minimize Z = w_E * D_E + w_G * D_G
D_E = T_E - T_E_free
D_G = 일반차 영향권 대당평균 지연
```

현재 최종 runner 기준:

| 위치 | 현재 동작 | 판정 |
| --- | --- | --- |
| `run_b0_b4_signal_pipeline.py` | runtime summary용 `objective_score = 10 * d_EMV_sec + 1 * d_veh_sec` | 운영 로그용 ratio score |
| `09-1 ... run_b4_optimization_s1forced.py` | `score_delay_row()`가 `d_EMV_sec`, `d_veh_sec`를 우선 사용하고 `w/(w_E+w_G)`로 정규화 | 최종 표/그림 정본 |
| `run_b4_theta_bo.py` | `score_for_row()`가 지연 필드를 우선 사용하고 `w/(w_E+w_G)`로 정규화 | 단일 BO도 최종 목적함수와 정렬 |

주의:

- `10:1` 입력은 코드 내부에서 `w_E=10/11`, `w_G=1/11`로 정규화해 score를 계산합니다.

## 최적화 정책 적용 상태

현재 `09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py`가 구현한 내용:

- fixed-budget `n=15`, `m=50`
- Random Search, 표준 `cma` 패키지 기반 CMA-ES, BO 비교
- `table1_best_so_far.csv`: 방법별/seed별 누적 best-so-far
- `table2_bo_surrogate.csv`: BO 관측값, surrogate mean/CI, acquisition
- `table3_pareto.csv`: `1:1`, `5:1`, `10:1`, `15:1`, `20:1` 가중치별 최적 후보
- `figure3_pareto.png`: Pareto 후보와 knee point 보조 표시
- `noise_check_5repeat.csv`: 실제 5회 반복 noise check

Pareto sweep 해석:

- 목적은 가중치를 정하는 것이 아니라 trade-off를 보여주는 것입니다.
- 각 가중치에서 조건은 동일해야 합니다.
- 각 가중치에 대해 BO 탐색 1회를 수행하고, SPC 기반으로 개선 변동이 잦아드는 지점에서 중단할 수 있습니다.
- knee point는 보조 표시이며 정책 결정을 대체하지 않습니다.

## 실제 실행/검증 상태

확인된 것:

- Stage1은 `B04_ad_stage23_trigger` B0 metric artifact를 읽도록 갱신했습니다.
- `b4_stage1_summary.json`의 provenance는 `primary_candidate=B04_ad_stage23_trigger`, `measurement_source_candidate=B04_ad_stage23_trigger`, `provenance_status=PASS`입니다.
- `requirements.txt`와 `verify_env.py`는 최적화 테스트에 필요한 `cma`, `matplotlib`, `numpy`, `sklearn`까지 포함하도록 갱신했습니다.
- `b04-validate --candidates B04_ad_stage23_trigger` 결과는 현재 `FAIL`입니다. green18 B04 검증망 기준으로 `emergency_arrived=true`, `emergency_teleport=false`, `background_teleported=0`, `stage23_teleported=0`, `base_background_teleported=0`까지 복구됐지만 `speed_sanity_fail_count=2`, `metric_invalid_count=0`, `free_count=12`, `speed_mae_kmh=30.403`, `travel_time_mae_s=45.028`, `queue_top10_overlap=5`입니다.
- S1-forced canonical net의 EV route uncontrolled minor connection 3개는 priority `M`으로 보정했습니다. 또한 `347237859#0`을 지나는 기존 비-Stage23 배경 수요를 제거해 Stage23 trigger만 남기면서 09-1 real smoke baseline gate를 통과했습니다. baseline row는 `final_status=PASS`, `termination_reason=ev_arrived_min_summary`, `T_actual_EMV_sec=451.0`, `emergency_arrived=true`, `emergency_teleport=false`, `background_teleported=0`입니다.

주의할 것:

- 최근 생성한 `B04_ad_stage23_trigger` B0 run은 EV/background teleport 문제와 09-1 smoke baseline gate를 해결했지만 green18 strict recall validation은 FAIL입니다. full 최적화 전 현실재현 MAE/queue recall 개선 여부를 별도로 판단해야 합니다.
- 실제 SUMO 기반 `s1forced_fixed_budget_n15_m50` 전체 실행이 완료됐다고 말할 수 없습니다.
- 실제 `table1_best_so_far.csv`, `table2_bo_surrogate.csv`, `table3_pareto.csv`, PNG 3개가 제출 가능한 값으로 생성됐는지는 full run 후 확인해야 합니다.

## 이상하거나 정리 필요한 부분

1. `B04_ad_stage23_trigger` baseline 자체의 validation 상태
   - Stage1 provenance는 맞지만, baseline validation은 현재 FAIL입니다.
   - 09-1 smoke는 Stage3 평가까지 넘어가지만, green18 strict recall validation은 아직 PASS가 아닙니다.

2. Case B 해석
   - 최신 Stage3 판단은 `runtime_tau_segment`, `runtime_tau_movement`, 또는 adjacency `tau * L` 기준입니다.
   - event log의 `case_b_source`를 보면 segment 기반인지 movement 기반인지 확인할 수 있습니다.

3. 일반차 영향권 `V_G`
   - 현재 일반차 지연은 background tripinfo와 free-time row 매칭 기반입니다.
   - 참조 문서의 영향권 edge set이 완전히 명시 고정됐다고 쓰려면 추가 감사가 필요합니다.

## 팀원에게 전달 가능한 표현

> `/Users/junlee/Desktop/js/1 4 최종_결정변수와 알고리즘 3773b21010b280f69473f5455be7ec01.md`의 최종 변수 정리와 Stage1/2/3 구조에 맞춰, B4 런타임 결정변수는 `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau` 5개로 맞춰져 있습니다. 09번 baseline의 active default도 S1-forced/stage23 입력과 정규화된 10:1 목적함수로 맞췄고, Random Search, 표준 `cma` 기반 CMA-ES, BO를 fixed-budget으로 비교하는 runner는 `09-1 B4 Optimization S1forced/`에 분리 구현했습니다. Pareto sweep은 가중치별 응급차/일반차 지연 trade-off를 보여주는 용도이며, 최종 가중치 선택은 정책 결정자의 몫입니다.

아래 표현은 아직 쓰면 안 됩니다.

- “현장 신호/보행 신호를 완전 재현했다.”
- “full `n=15`, `m=50` 실제 SUMO 최적화가 완료됐다.”
- “30회 반복 검증을 수행했다.”
- “knee point 또는 10:1이 정답이다.”
