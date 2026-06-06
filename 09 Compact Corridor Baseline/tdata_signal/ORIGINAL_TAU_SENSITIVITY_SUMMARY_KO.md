# 원래 Tau 지표 복구 및 민감도 분석

## 변경 내용

`B4ThetaParams`에서 tau의 의미를 다시 `S구간 queue / S구간 길이`로 복구했다.

기존 동작:

- tau가 `local_fill_100m` threshold로 사용됨
- tau가 커질수록 speed trigger threshold도 같이 낮아짐
- 결과적으로 tau sweep이 queue/L 민감도만 보여주지 못함

복구 후 동작:

- `theta_runtime_thresholds()`는 더 이상 tau로 local-fill/speed threshold를 바꾸지 않음
- Case B 후보 movement는 `case_b_segment_fill = S구간 queue_proxy_m / L_b0_m`가 tau 이상일 때만 Stage3 후보가 됨
- S7/S10/S11 Case B 후보에 대해 원래 Tau를 적용함

## 검증

- `python3 -m py_compile` PASS
- `python3 -m unittest tests.test_b4_runtime_contract tests.test_b4_theta_bo` PASS
- 총 44개 테스트 PASS

## Strict Original Tau Sweep

조건:

- net: `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- demand: `/Users/junlee/Desktop/js/data_prepared/compact_v9/demand/background_routes_compact_v9_B04_reality_4000_sustained.rou.xml`
- hard max sim time: 4000초
- `t_lead=5`, `ext_max=5`, `hold_max=5`, `d_up=1`
- tau: 0.65, 0.70, 0.75, 0.80, 0.85

결과:

| tau | status | termination | EV travel time | stage3 preemption | original tau phase changes | mean delay |
|---:|---|---|---:|---:|---:|---:|
| 0.65 | FAIL | emergency_stuck | - | 0 | 0 | 94.229 |
| 0.70 | FAIL | emergency_stuck | - | 0 | 0 | 94.229 |
| 0.75 | FAIL | emergency_stuck | - | 0 | 0 | 94.229 |
| 0.80 | FAIL | emergency_stuck | - | 0 | 0 | 94.229 |
| 0.85 | FAIL | emergency_stuck | - | 0 | 0 | 94.229 |

## 왜 반응하지 않았는가

strict run의 사후 laneData 기준 S구간 fill은 다음 수준이었다.

| segment | max fill | p95 fill | tau 0.65 hit |
|---|---:|---:|---:|
| S7 | 0.512 | 0.435 | 0 |
| S10 | 0.211 | 0.187 | 0 |
| S11 | 0.025 | 0.023 | 0 |

따라서 현재 demand와 EV 출발 600초 조건에서는 원래 Tau가 0.65 이상으로 차기 전에 EV가 stuck 판정 구간에 들어간다. 즉 0.65~0.85 sweep은 민감하지 않은 것이 아니라, 모두 threshold 미달이라 제어가 발생하지 않는다.

## 해석

현재 맵/수요에서 원래 Tau를 단독 Stage3 trigger로 쓰는 것은 부적합하다.

- `0.65`도 너무 높다.
- S7은 no-control이나 기존 B4 결과에서는 나중에 크게 막히지만, strict original Tau 제어가 필요한 시점에는 아직 0.65까지 차지 않는다.
- 기존 B4가 작동한 이유는 원래 Tau가 아니라 local-fill/speed trigger가 먼저 반응했기 때문이다.

## 권장 방향

원래 Tau 정의를 살리려면 아래 중 하나가 필요하다.

1. tau 후보 범위를 낮춘다: `0.25, 0.35, 0.45, 0.55, 0.65`
2. strict trigger가 아니라 hybrid trigger로 둔다: `S구간 tau OR low_speed`
3. tau 측정 구간을 현재 Case B 후보 S7/S10/S11에만 두지 말고, EV route 전체 S구간으로 확장한다.
4. stuck 판정 전에 충분한 queue가 형성되도록 EV 출발시각 또는 warm-up 수요를 조정한다.

현재 상태에서 BO 후보로는 `0.65~0.85`보다 `0.25~0.65`가 더 유효하다.

## 산출물

- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/original_tau_strict_sweep_results.csv`
- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/original_tau_strict_sweep_results.json`
- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/original_tau_sweep_results.csv`
- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/original_tau_sweep_results.json`
