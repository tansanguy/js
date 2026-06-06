# B04/B4 Target15 u130 Toegye15 Change Log

작성일: 2026-06-06

## 목적

기존 `active15` 수요는 EV 평균속도 15 km/h를 맞추기 위해 전체 수요를 약 0.4배로 낮춘 성격이 강했다. 이로 인해 구간별 정체 재현과 `tau`/Case B 민감도가 약해졌다. 이번 변경은 기존 `u130` 수요를 기반으로 총량을 유지하면서, 퇴계로를 타는 일반차 기준 평균속도 15 km/h를 맞추고 B4 Stage 3 및 original `tau`가 실제 런타임에서 반응하도록 만드는 데 초점을 둔다.

## 반영된 알고리즘 구조

초기 알고리즘에서 선별한 결정변수는 다음 5개로 유지한다.

| 변수 | 역할 | 현재 반영 |
| --- | --- | --- |
| `alpha` | EV ETA buffer | B4 theta/OFAT/BO 입력으로 유지 |
| `t_lead` | 선행 점등 시점 | B4 theta/OFAT/BO 입력으로 유지 |
| `delta_T_thr` | 선점 트리거 게이트 | B4 theta/OFAT/BO 입력으로 유지 |
| `G_ext` | EV 통과 후 녹색 연장 | B4 theta/OFAT/BO 입력으로 유지 |
| `Q_trig` | Stage 2 merge 개입 임계 | B4 theta/OFAT/BO 입력으로 유지 |

`tau`, `tau_scale`, `tau_numerator_gamma`, `hold_max`, `d_up`는 결정변수가 아니라 고정 구조 파라미터 또는 민감도 확인 대상이다. 새 fixed-parameter sensitivity와 theta OFAT 기본 입력은 `u130_toegye15` 수요와 이에 맞춘 Stage1으로 변경했다.

## 주요 변경 사항

1. `u130` 기반 일반차 Target15 수요 생성

- 기준 파일: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml`
- 최종 파일: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130_toegye15.rou.xml`
- 차량 총량: `4508`대 유지
- route 재배치: `0`대
- 시간대 재배치: synthetic clone `1040`대 중 `219`대만 shoulder 시간대로 이동
- 선택 프로필: `u130_shoulder_p20`

2. 일반차 퇴계로 평균속도 metric 변경

도착차 tripinfo 평균은 정체 차량을 누락하므로, `edgeData` 기반 B04 Toegye movement speed를 movement entered count로 가중한 평균을 calibration metric으로 사용했다.

최종 선택 결과:

| 항목 | 값 |
| --- | ---: |
| 목표 평균속도 | `15.0 km/h` |
| 달성 평균속도 | `15.267543 km/h` |
| B04 EV 통행시간 | `1121 sec` |
| B04 EV 평균속도 | `13.079368 km/h` |
| background arrived ratio | `0.129547` |
| general mean delay | `187.913579 sec` |

3. B04 provenance 기반 Stage1 재생성

새 Stage1 디렉터리:

- `data_prepared/compact_v9/b4_stage1_u130_toegye15`

이 Stage1은 같은 B04 no-control run의 `edgeData`, `laneData`, `tripinfo`, `net`, `background_route`를 provenance로 가진다. 따라서 이전에 남아 있던 B0 산출물과 실제 실행 net/demand 간 provenance mismatch를 줄인다.

Stage1 측정 요약:

| 항목 | 값 |
| --- | ---: |
| segment count | `44` |
| speed <= 15 km/h count | `17` |
| physical queue-like count | `17` |
| Stage2 merge support | `weak_runtime_required` |

4. Stage 3 및 original tau 반응 확인

새 수요와 새 Stage1으로 B4 1회 확인 run을 수행했다.

| 항목 | 값 |
| --- | ---: |
| B4 final status | `FAIL` |
| failure reason | `emergency_stuck` |
| Stage3 preemption count | `31` |
| bottleneck mode count | `27` |
| Stage2 hold count | `2` |
| signal burden | `1409.009441 sec` |
| original tau case-b source rows | `72` |
| tau 포함 phase-change trigger reason | `13` |

해석: 새 수요는 Stage3와 original `tau`를 충분히 활성화한다. 다만 현재 기본 제어인자(`alpha=1.15`, `t_lead=21`, `delta_T_thr=80`, `G_ext=32`, `Q_trig=0`)로는 정체가 강해 EV가 `347237859#4` 부근에서 stuck 된다. 따라서 이 수요는 최적화/민감도 실험을 위한 stress scenario로 적합하지만, 기본 theta는 재탐색이 필요하다.

5. fixed-parameter sensitivity 구조 lock 보조 로직

`run_b4_fixed_param_sensitivity.py`에 구조 파라미터 OFAT 결과를 후보별로 roll-up하고, baseline 대비 score/EV 개선 및 signal burden gate를 적용해 `tau`, `tau_scale`, `tau_numerator_gamma`, `hold_max`, `d_up`의 lock 후보를 요약하는 helper를 추가했다. `combined_lock` 후보 생성 함수도 포함되어 있어, 개별 OFAT에서 선택된 구조 후보를 조합 run으로 재확인할 수 있다.

주의: 현재 추가된 lock helper는 요약/선정 로직이며, 전체 실행 루프에서 combined confirmation을 자동 추가 실행하는 단계는 후속 wire-up이 필요하다.

## 추가된 주요 파일

- `09 Compact Corridor Baseline/calibrate_u130_toegye_general15.py`
- `09 Compact Corridor Baseline/build_b4_stage1_from_b04_run.py`
- `09 Compact Corridor Baseline/run_b4_fixed_param_sensitivity.py`
- `09 Compact Corridor Baseline/run_b4_theta_ofat_sensitivity.py`
- `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130_toegye15.rou.xml`
- `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130_toegye15.rou.summary.json`
- `data_prepared/compact_v9/b4_stage1_u130_toegye15/`
- `09 Compact Corridor Baseline/tdata_signal/u130_toegye_general15_selected/`

## 검증

수행한 확인:

- `python3 -m py_compile` for new/updated sensitivity scripts
- B04 no-control calibration sweep for u130-derived demand variants
- B04 selected run for `u130_shoulder_p20`
- Stage1 rebuild from selected B04 run
- B4 one-run check with fixed baseline theta

주의:

- B4 check는 의도적으로 stress scenario 확인용이며 PASS가 아니다.
- 다음 단계는 이 수요와 Stage1을 기준으로 `theta` OFAT/BO를 다시 수행하는 것이다.
- `tau`는 이제 이벤트와 일부 phase-change 원인에 반영되지만, 최종 목적함수 민감도는 별도 OFAT로 재검증해야 한다.

## 다음 강화 방향

1. `u130_toegye15` 기준 theta OFAT를 먼저 실행해 `t_lead`, `delta_T_thr`, `G_ext`의 feasible range를 좁힌다.
2. B4가 stuck 되는 `347237859#4` 주변의 Stage1 movement와 same-lane blocker flush 정책을 별도로 점검한다.
3. `tau`를 결정변수로 승격하지는 말고, `tau_scale`, `tau_numerator_gamma`와 함께 fixed-structure sensitivity를 수행해 고정값의 방어 가능성을 확보한다.
4. Stage2 merge는 여전히 `weak_runtime_required`이므로 B0 평균값 대신 runtime `n_occ`/`Lq_merge`를 주 신호로 유지한다.
