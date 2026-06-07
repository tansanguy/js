# B4 구현 감사 후속 점검 목록

작성 기준: 2026-06-07 현재 코드베이스

참조:

- `/Users/junlee/Desktop/js/1 4 최종_결정변수와 알고리즘 3773b21010b280f69473f5455be7ec01.md`
- `09 Compact Corridor Baseline/B4_FINAL_DECISION_VARIABLES_IMPLEMENTATION_AUDIT_KO.md`
- `09 Compact Corridor Baseline/B4_09_RUN_CONDITIONS_AUDIT_KO.md`

이 문서는 구현 적합도 감사 결과에서 바로 실행 전에 확인해야 할 것, 아직 부족한 것, 결과 해석 시 의심해야 할 것을 분리한 점검표입니다.

## 확인할 것

1. 환경 의존성
   - `python -m pip install -r requirements.txt`를 실행합니다.
   - `python 00_setup/verify_env.py`가 `traci`, `sumolib`, `yaml`, `numpy`, `sklearn`, `cma`, `matplotlib`를 모두 PASS하는지 확인합니다.

2. 자동 감사
   - `python "09-1 B4 Optimization S1forced/audit_09_run_conditions.py"` 결과가 `FAIL=0`, `WARN=0`, `INFO=0`인지 확인합니다.
   - Stage1 primary candidate와 measurement source가 모두 `B04_ad_stage23_trigger`인지 확인합니다.

3. Stage1 provenance
   - `data_prepared/compact_v9/b4_stage1_s1forced/b4_stage1_summary.json`에서 아래 값을 확인합니다.

```text
primary_candidate = B04_ad_stage23_trigger
measurement_source_candidate = B04_ad_stage23_trigger
provenance_status = PASS
```

4. 핵심 테스트
   - `python -m pytest tests/test_b4_09_run_conditions_audit.py -q`
   - `python -m pytest tests/test_b4_theta_bo.py tests/test_b4_stage1_contract.py tests/test_b4_runtime_contract.py -q`
   - `python -m pytest tests/test_b4_optimization_s1forced.py -q`

5. Mock smoke
   - `run_b4_optimization_s1forced.py --mock-eval` 실행 후 `all_evaluations.csv`, `table1_best_so_far.csv`, `table2_bo_surrogate.csv`, `table3_pareto.csv`, PNG 3개가 생성되는지 확인합니다.

6. Real smoke
   - `n=1`, `m=4`, `--skip-pareto`, `--skip-noise-check` real smoke는 baseline gate를 통과하고 평가 CSV/PNG를 생성합니다.
   - baseline 확인값은 `final_status=PASS`, `termination_reason=ev_arrived_min_summary`, `T_actual_EMV_sec=451.0`, `emergency_arrived=true`, `emergency_teleport=false`, `background_teleported=0`입니다.
   - full `n=15`, `m=50` 실행 전에는 green18 strict recall FAIL을 허용 가능한 현실재현 한계로 문서화할지, 추가 calibration을 할지 결정해야 합니다.

## 부족한 것

1. `B04_ad_stage23_trigger` baseline validation 재확인
   - Stage1 provenance는 최신 후보로 맞췄습니다.
   - 현재 validation은 FAIL입니다.
   - green18 B04 검증망 확인값: `emergency_arrived=true`, `emergency_teleport=false`, `background_teleported=0`, `stage23_teleported=0`, `base_background_teleported=0`, `speed_sanity_fail_count=2`, `metric_invalid_count=0`, `free_count=12`, `speed_mae_kmh=30.403`, `travel_time_mae_s=45.028`, `queue_top10_overlap=5`.
   - 09-1 S1-forced baseline 확인값: `final_status=PASS`, `termination_reason=ev_arrived_min_summary`, `T_actual_EMV_sec=451.0`, `emergency_arrived=true`, `emergency_teleport=false`, `background_teleported=0`.
   - 남은 판단은 strict recall PASS를 계속 목표로 둘지, 09-1 최적화 실행 가능성을 우선할지입니다.

2. 가중치 표기
   - 실행 인자는 `10:1` 같은 ratio를 받습니다.
   - score 계산은 내부에서 `10:1 = 10/11 : 1/11`처럼 정규화합니다.

3. 실제 full optimization 산출물
   - full `s1forced_fixed_budget_n15_m50` 실제 SUMO 결과가 생성됐다고 주장하려면 `outputs/{run_id}` 아래 CSV/PNG와 row 수를 확인해야 합니다.
   - noise check는 실제 5회 artifact만 있습니다. 30회 반복을 수행하지 않았다면 30회라고 쓰면 안 됩니다.

## Pareto Sweep 점검

목적:

- 가중치 변화에 따라 응급차 지연과 일반차 지연이 맞교환되는 정도를 보여줍니다.
- knee point는 합리적인 후보 지점을 설명하는 보조 표시입니다.
- 최종 결정은 정책 결정자의 몫입니다.

표 양식:

| 가중치(w1:w2) | 최적 theta | delay_A | delay_N |
| --- | --- | --- | --- |
| 1:1 | `table3_pareto.csv` | 결과값 | 결과값 |
| 5:1 | `table3_pareto.csv` | 결과값 | 결과값 |
| 10:1 | `table3_pareto.csv` | 결과값 | 결과값 |
| 15:1 | `table3_pareto.csv` | 결과값 | 결과값 |
| 20:1 | `table3_pareto.csv` | 결과값 | 결과값 |

확인 기준:

- 가중치 외의 net, demand, Stage1, 사고 위치, 출동 조건이 모두 동일해야 합니다.
- 각 가중치에 대해 BO 탐색 1회가 수행되어야 합니다.
- SPC 기반 조기 중단이 적용된 경우 `rounds_completed`, `spc_stop_recommended`, `spc_stop_round`를 같이 보고합니다.
- 값이 튀는 경우에만 같은 가중치의 반복 탐색을 추가합니다.
- 붉은 knee point를 “정답”이나 “채택 결론”으로 설명하지 않습니다.

## 의심스러운 것

1. Case B 해석
   - 최신 Case B 판정 경로는 runtime queue/segment fill과 결정변수 `tau` 기준으로 정리했습니다.
   - 최신 Stage3 판단은 runtime queue/segment fill과 결정변수 `tau` 기준입니다.
   - 실제 event log의 `case_b_source`를 확인하면 `runtime_tau_segment`, `runtime_tau_movement`, adjacency Case B 중 어떤 근거로 발동했는지 알 수 있습니다.

2. 일반차 영향권 `V_G`
   - 참조 문서의 `V_G = 본선 + 본선 교차로 incoming 지류 edge` 정의가 코드에서 명시 edge set으로 완전히 고정돼 있다고 단정하기 어렵습니다.
   - 현재 일반차 지연은 background tripinfo와 free-time row 매칭 기반입니다.

3. CMA-ES 명칭
   - `09-1` runner의 CMA-ES는 표준 Python `cma` 패키지의 `CMAEvolutionStrategy`를 사용합니다.

## 최종 제출 전 최소 PASS 기준

```bash
python "09-1 B4 Optimization S1forced/audit_09_run_conditions.py"
```

```bash
python -m pytest tests/test_b4_09_run_conditions_audit.py tests/test_b4_optimization_s1forced.py -q
```

```bash
python -m pytest tests/test_b4_theta_bo.py tests/test_b4_stage1_contract.py tests/test_b4_runtime_contract.py -q
```

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
