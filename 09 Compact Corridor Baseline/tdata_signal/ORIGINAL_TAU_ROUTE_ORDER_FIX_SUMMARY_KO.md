# Original Tau Route-Order Fix 및 재실험 요약

## 수정 내용

기존 original Tau strict 실험은 `S9:upbound` 병목을 놓쳤다. 이를 고치기 위해 다음을 수정했다.

- `B4Movement`에 `local_storage_edges`, `corridor_storage_edges`를 저장
- 모든 `mapped_S_segment` movement에 대해 original Tau 계산
- Case B 후보 3개(S7/S10/S11)에만 의존하지 않음
- route order 기반으로 corridor storage edge별 queue proxy를 계산하고 합산
- `B4ThetaParams`일 때 Stage3 trigger를 `original_tau_fill >= tau`로 판단
- 이벤트 로그에 다음 컬럼 추가
  - `original_tau_segment_id`
  - `original_tau_queue_m_proxy`
  - `original_tau_fill`
  - `original_tau_denominator_m`
  - `original_tau_source`

## 검증

- `python3 -m py_compile` PASS
- `python3 -m unittest tests.test_b4_runtime_contract tests.test_b4_theta_bo` PASS
- 총 45개 테스트 PASS

## 핵심 디버그 결과

수정 전에는 EV가 `218773869#6`, route index 34에서 stuck 됐지만, original Tau 후보가 S7/S10/S11만 보고 있어서 실제 병목인 S9를 놓쳤다.

수정 후에는 `B4_MOVEMENT_06/07`, `S9:upbound`가 original Tau 대상에 들어갔고, 이벤트 로그에서 S9 fill이 초반부터 높게 잡힌다.

예시:

- segment: `S9:upbound`
- movement: `B4_MOVEMENT_06`
- time 616초 original_tau_fill: 0.94344
- time 619초 original_tau_fill: 0.98924
- denominator: 250.0 m
- source: `route_order_corridor_edge_queue_sum`

따라서 이전의 `S10 0.211`, `S11 0.025` 문제는 실제 차량 부족이 아니라 segment mapping/proxy 오류였다.

## Route-Order Original Tau Sweep

조건:

- net: `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- demand: `/Users/junlee/Desktop/js/data_prepared/compact_v9/demand/background_routes_compact_v9_B04_reality_4000_sustained.rou.xml`
- hard max sim time: 4000초
- `t_lead=5`, `ext_max=5`, `hold_max=5`, `d_up=1`
- tau: 0.65, 0.70, 0.75, 0.80, 0.85

| tau | status | EV travel time | Stage3 preemption | original tau phase changes | max original tau fill | mean delay |
|---:|---|---:|---:|---:|---:|---:|
| 0.65 | PASS | 521.0 | 3 | 3 | 1.000 | 157.820 |
| 0.70 | PASS | 521.0 | 3 | 3 | 1.000 | 157.820 |
| 0.75 | PASS | 526.0 | 3 | 3 | 0.989 | 161.862 |
| 0.80 | PASS | 578.0 | 3 | 3 | 0.989 | 162.915 |
| 0.85 | PASS | 622.0 | 4 | 4 | 0.989 | 181.371 |

## 민감도 해석

수정 후 `0.65~0.85`는 반응한다.

- `0.65`와 `0.70`은 동일하게 가장 좋음: EV 521초
- `0.75`는 거의 비슷하지만 5초 느림
- `0.80`부터 EV가 뚜렷하게 느려짐: 578초
- `0.85`는 preemption이 하나 늘었지만 EV와 background 모두 악화: EV 622초, mean delay 181.371초

현재 조건에서는 `tau=0.65~0.70`이 가장 안정적이다.

## Segment별 제어

original tau phase change 기준:

- tau 0.65: `S9:upbound` 2회, `S15:upbound` 1회
- tau 0.70: `S9:upbound` 2회, `S15:upbound` 1회
- tau 0.75: `S9:upbound` 2회, `S15:upbound` 1회
- tau 0.80: `S9:upbound` 2회, `S15:upbound` 1회
- tau 0.85: `S9:upbound` 2회, `S15:upbound` 1회, `S14:upbound` 1회

즉 실제 주요 병목인 S9가 이제 Tau trigger로 잡힌다.

## 산출물

- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/original_tau_route_sweep_results.csv`
- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/original_tau_route_sweep_results.json`
- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/ORIGINAL_TAU_ROUTE_ORDER_FIX_SUMMARY_KO.md`
