# 10 Final Destination Validation

10번 폴더는 `검증용 시뮬레이션`입니다.

9-1에서 고른 B4 theta를 다시 최적화하지 않고, 실제 목적지 3곳에서 B004/B04/B4를 반복 비교합니다. 10번 실행 중에는 BO, CMA-ES, Random Search를 다시 돌리지 않습니다.

## 1. 입력 기준

| 항목 | 값 |
| --- | --- |
| active inputs | `configs/compact_v9_B04_B4_active_inputs.json` |
| net | `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml` |
| demand | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml` |
| Stage1 | `data_prepared/compact_v9/b4_stage1_s1forced` |
| theta source | `09-1 B4 Optimization S1forced/outputs/latest.json` 또는 명시한 `all_evaluations.csv` |
| decision variables | `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau` |

제출용 실행에서는 9-1 full fixed-budget 결과의 `all_evaluations.csv`를 직접 지정하는 것을 권장합니다.

## 2. 실행 계획

1. 9-1 결과에서 좋은 theta를 선택합니다.
2. `05_theta_check_simulation/routes/b0_valid_18_routes.csv`의 18개 목적지 후보를 읽습니다.
3. 최신 net에서 각 후보 목적지까지 route를 다시 만듭니다.
4. screening phase에서 각 후보를 B004 1회, B04 1회, B4 1회 실행합니다.
5. 개선폭, B04 지연, 실제 개입량, 대표성을 기준으로 최종 3개 목적지를 고릅니다.
6. final phase에서 최종 3개 목적지를 30회 반복 검증합니다.

목적지 선택에는 ESSI를 쓰지 않습니다. ESSI는 9-1 BO에서 다음 theta를 고를 때 쓰는 요소이고, 10번은 screening 결과로 목적지를 고릅니다.

## 3. 실행 명령

구조만 확인하는 dry-run:

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --dry-run \
  --run-id validation_simulation_dry_run
```

제출용 전체 실행:

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv" \
  --theta-method ALL \
  --workers 6 \
  --run-id validation_simulation_001
```

기존 이름을 유지해야 하면 `--run-id final_destination_validation_001`을 사용해도 됩니다.

screening만 먼저 실행:

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase screening \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv" \
  --theta-method ALL \
  --workers 6 \
  --run-id validation_simulation_001
```

같은 run-id의 screening 결과로 final만 실행:

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase final \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv" \
  --theta-method ALL \
  --workers 6 \
  --run-id validation_simulation_001
```

## 4. 산출물

기본 위치는 `results/metrics/compact_v9_final_destination_validation/{run_id}/`입니다.

| 파일 | 의미 |
| --- | --- |
| `screening/candidate_selection.csv` | 18개 후보 screening 결과 |
| `screening/task_manifest.csv` | screening 실행 계획 |
| `final/candidate_selection.csv` | 최종 3개 목적지의 30회 검증 요약 |
| `final/final_simulation_results.csv` | 제출/보고용 최종 검증 표 |
| `final/selected_route_runs.csv` | 최종 목적지의 B004/B04/B4 run row |
| `final/selected_mode_averages.csv` | route/mode별 평균 |
| `final/selected_destinations.json` | 실제 선택된 3개 목적지 |
| `final/spc_repeat_stability.csv` | 30회 반복 안정성 판단 |
| `final/final_destination_validation_report.md` | 최종 보고서 |
| `experiment_summary.json` | 전체 실행 요약 |

## 5. 결과 설명 기준

- 10번은 theta를 새로 찾는 단계가 아니라 검증 단계입니다.
- B004는 자유류 기준, B04는 무제어 baseline, B4는 잠근 theta를 적용한 제어 run입니다.
- final phase에서 목적지 3개가 각각 30회 반복됐을 때만 30-repeat 최종 검증이라고 설명합니다.
- `theta_source_smoke_warning=true`이면 smoke 결과를 쓴 것이므로 제출용 결과로 설명하지 않습니다.

## 6. 확인

문서와 runner 계약을 확인하는 최소 테스트:

```bash
.venv/bin/python -m pytest tests/test_b4_optimization_s1forced.py tests/test_final_destination_validation.py -q
```
