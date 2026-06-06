# A008 위치 매칭 T-Data 신호망 검증 요약

작성 시각: 2026-06-05T12:14:03.572210+00:00

## 구현 범위

A008_P.csv의 교차로번호를 T-Data API itstId 후보로 사용하고, EPSG:5186 좌표를 WGS84로 변환해 mainstream skeleton endpoint와 거리 기반으로 매칭했다.
개별 itstId API 호출은 쓰지 않고 batch snapshot을 수집한 뒤 로컬에서 matched itstId를 필터링했다.

## 주요 산출물

- endpoint 매핑: `09 Compact Corridor Baseline/tdata_signal/a008_mainstream_itst_mapping.csv`
- TLS 매핑: `09 Compact Corridor Baseline/tdata_signal/a008_tls_itst_mapping.csv`
- 신호 프로파일: `09 Compact Corridor Baseline/tdata_signal/location_matched_signal_profiles.csv`
- TraCI 메타데이터: `09 Compact Corridor Baseline/tdata_signal/location_matched_traci_signal_metadata.json`
- 생성 net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_location_matched.net.xml`

## 매칭/API 요약

- skeleton endpoint 수: 23
- endpoint 자동 확정 수: 21
- TLS 위치 매칭 수: 16
- T-Data timing hit 수: 2
- T-Data state hit 수: 0
- active B04 net 덮어쓰기: 완료
- active net 백업: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_green18.before_location_matched.net.xml`

## SUMO 구조 검증

- SUMO load: PASS
- TLS integrity: PASS (error 0, warn 4 single-phase)
- firetruck route connectivity: PASS (bad pair 0)

## B04/B4 결과

| mode | status | termination | EV time | EV arrived | teleport | background ratio |
| --- | --- | --- | ---: | --- | --- | ---: |
| B004 | REFERENCE | analytic_reference_complete | 217.98 | True | False |  |
| B04 | PASS | ev_arrived_min_summary | 884.0 | True | False | 0.329493 |
| B4 | PASS | recovery_timeout | 568.0 | True | False | 0.327957 |

B4는 B04 no-control 대비 EV travel time이 316.0초 짧았고, 두 실행 모두 EV teleport는 없었다.

## 해석

이 신호망은 현장 완전 재현이 아니라 실제 위치 itstId 기반 plausible 신호망이다. API snapshot에 없는 A008 교차로는 fallback 프로파일로 표시된다.
