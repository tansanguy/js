# 10 Final Destination Validation

10번은 9번에서 결정된 B4 theta를 다시 최적화하지 않고, 실제 목적지 3곳으로 보내는 최종 성능 검증 실험입니다. 실행 중 BO, CMA-ES, Random Search는 돌리지 않고 ESSI로 목적지를 고르지도 않습니다.

## 1. 9번 반영 기준

| 항목 | 값 |
| --- | --- |
| active inputs | `configs/compact_v9_B04_B4_active_inputs.json` |
| net | `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml` |
| demand | `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml` |
| Stage1 | `data_prepared/compact_v9/b4_stage1_s1forced` |
| signal/route audit | `09 Compact Corridor Baseline/tdata_signal/summaries/b04_global_reality_signal_summary.json` |
| route geometry audit | `09 Compact Corridor Baseline/tdata_signal/route_geometry_recall_audit.json` |
| lane/TLS audit | `mainroad_lane_recall_audit.csv`, `route_internal_lane_alignment_audit.csv`, `route_tls_projection_audit.csv` |
| theta source | `09-1 B4 Optimization S1forced/outputs/latest.json` 또는 명시한 `all_evaluations.csv` |
| decision variables | `t_lead`, `delta_T_thr`, `G_ext`, `Q_ratio`, `tau` |

`alpha`, `Q_trig`는 legacy 입력 호환 필드일 뿐 10번의 최종 theta 설명에 쓰지 않습니다.

10번 파이프라인의 기본 `--net`, `--background-route`, `--base-stage1-dir` 값은 `configs/compact_v9_B04_B4_active_inputs.json`에서 읽습니다. `task_manifest.json`에는 active inputs, net, demand, Stage1, mainroad mapping 경로가 기록되므로 실행 후에도 최신 도로 경로/신호망이 붙었는지 확인할 수 있습니다.

## 2. 3개 지점 선정

3개 지점은 고정 목록을 재사용하지 않고 10번 조건에서 재선별합니다.

1. `screening`: `05_theta_check_simulation/routes/b0_valid_18_routes.csv`의 18개 target edge를 최신 Compact V9 소방서 출발 edge에서 다시 shortest route로 만든다.
2. 최신 S1-forced net에 target edge가 없거나 route가 연결되지 않는 후보는 `EXCLUDED_PRECHECK`로 남긴다. 2026-06-07 현재 `ER_ACC_018`의 `301285277#0`은 최신 net에 없어 task 실행 대상에서 제외된다.
3. 실행 가능한 후보를 B004 1회, B04 1회, B4 1회 paired departure로 실행한다.
4. 아래 조건을 만족하는 후보만 최종 후보로 둔다.
   - EV 도착 성공
   - emergency teleport 없음
   - B004/B04/B4 비교 가능
   - `B4_vs_B04_D_E_improvement_sec > 0`
   - `stage2_hold_mean + stage3_preemption_mean > 0`
5. 개선폭, B04 지연 크기, 실제 개입량, mainroad/spine 대표성 순서로 상위 3개를 확정한다.

ESSI는 9-1 BO acquisition 구성요소입니다. 10번의 3개 목적지 선택 기준은 screening 결과의 개선폭, B04 지연, 개입량, 대표성입니다.

지형 기준 예비 상위 후보는 `ER_ACC_019`, `ER_ACC_015`, `ER_ACC_006`입니다. 다만 기존 smoke에서 `ER_ACC_019` 실패가 확인됐으므로 최종 3곳은 반드시 screening 결과로 확정합니다.

## 3. 실행

전체 절차를 한 번에 확인합니다.

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --workers 6 \
  --run-id final_destination_validation_001
```

제출용 full run은 9-1 full fixed-budget 결과의 `all_evaluations.csv`를 명시하는 것을 권장합니다.

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --theta-all-evaluations "09-1 B4 Optimization S1forced/outputs/s1forced_fixed_budget_n15_m50/all_evaluations.csv" \
  --theta-method ALL \
  --workers 6 \
  --run-id final_destination_validation_001
```

screening만 먼저 실행할 수 있습니다.

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase screening \
  --workers 6 \
  --run-id final_destination_validation_001
```

같은 `run-id`의 screening 결과로 final 30-repeat 검증만 실행할 수 있습니다.

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase final \
  --workers 6 \
  --run-id final_destination_validation_001
```

구조와 task manifest만 확인하려면 dry-run을 사용합니다.

```bash
.venv/bin/python "10 Final Destination Validation/final_destination_validation.py" \
  --phase all \
  --dry-run \
  --run-id final_destination_dry_run
```

## 4. 산출물

기본 위치는 `results/metrics/compact_v9_final_destination_validation/{run_id}/`입니다.

| 파일 | 의미 |
| --- | --- |
| `screening/candidate_selection.csv` | 18개 후보의 screening 결과와 제외/선정 사유 |
| `final/candidate_selection.csv` | 최종 3개 지점의 30-repeat 검증 요약 |
| `final/final_simulation_results.csv` | 제출/보고용 최종 시뮬레이션 clean CSV. B4 repeat row만 input, `output_D_E_sec`, `output_D_G_sec`, normalized weight, score, 실측값, Stage2/Stage3 on 횟수로 기록 |
| `final/selected_route_runs.csv` | 최종 3개 지점의 B004/B04/B4 run row. 목적함수 필드는 `D_E_sec`, `D_G_sec`입니다. |
| `final/selected_mode_averages.csv` | mode별 평균 지표. 목적함수 평균은 `D_E_mean_sec`, `D_G_mean_sec`입니다. |
| `final/selected_destinations.json` | 실제 선택된 3개 지점과 route edge |
| `final/spc_repeat_stability.csv` | final 30-repeat 결과의 route별 SPC 안정성 판단 |
| `final/final_destination_validation_report.md` | 3개 지점이 무엇이고 왜 선택됐는지 설명하는 보고서 |
| `*/task_manifest.json` | 해당 phase가 사용한 active inputs, net, demand, Stage1 경로와 planned task |

SPC는 final 30-repeat 결과 안정성 판단에만 적용합니다. 보고서와 `spc_repeat_stability.csv`에는 route/metric별 `stable`, `active`, `insufficient` 상태가 기록됩니다.

`final_simulation_results.csv`에서는 `output_D_E_sec`, `output_D_G_sec` 뒤에 `weight_E`, `weight_G`, `weight_ratio`를 두고 그 바로 오른쪽에 `score`를 둡니다. `weight_E`, `weight_G`는 합이 1인 정규화 가중치이고, 원 ratio는 `weight_ratio`에 둡니다.

`theta_source_smoke_warning=true`이면 smoke 산출물에서 theta를 읽은 것입니다. 이 경우 제출용 최종 결과로 설명하지 말고 full fixed-budget 산출물을 지정해 다시 실행합니다.
