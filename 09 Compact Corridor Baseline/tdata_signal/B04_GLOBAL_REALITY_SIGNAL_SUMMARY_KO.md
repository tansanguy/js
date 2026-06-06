# B04 전역 현실형 신호망 적용 요약

## 적용 범위

- 대상 active B04 net: `/Users/junlee/Desktop/js/data_prepared/compact_v9/net/jungbu_compact_v9_B04_green18.net.xml`
- 생성 net: `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- 기존 active net 백업: `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_green18.before_global_reality_mild.net.xml`

현재 B04 net 안의 TLS 63개 전체에 현실형 신호 profile을 적용했다. 다만 이 결과는 "현장 신호 완전 재현"이 아니라, A008 위치 master와 T-Data snapshot에서 얻은 실측 잔여시간/상태를 활용한 plausible 전역 신호망이다.

## 신호 적용 방식

- 메인도로 TLS 16개: 기존 위치 매칭 신호를 보존
- 직접 API timing 매칭 TLS 13개: A008 최근접 itstId가 T-Data snapshot 안에서 발견된 값 사용
- 유사 조건 fallback TLS 34개: 가까운 직접/API 기반 source profile을 가져와 기존 phase 구조와 비율을 유지한 채 cycle/offset만 현실형으로 보정
- 평균 fallback TLS 0개

전역 전체 평균 profile은 cycle 107초, 주 green 71초, yellow 3초였다. 최종 mild 버전은 엄격한 전체 phase 치환 대신 기존 SUMO phase 구조를 보존하면서 현실형 cycle만 이식했다. 엄격한 전역 치환 버전은 B04/B4 smoke에서 과도한 정체를 만들었기 때문에 active net에는 적용하지 않았다.

## 검증 결과

- SUMO net load: PASS
- TLS integrity: PASS
- TLS 개수: 63개
- 단일 phase TLS repair: 1개
- firetruck + background route load: PASS

## B04/B4 Smoke

실행 수요는 4000초 max simulation에 맞춰 3468대 background 차량을 180-3900초 구간에 지속 투입한 파일을 사용했다.

- demand: `/Users/junlee/Desktop/js/data_prepared/compact_v9/demand/background_routes_compact_v9_B04_reality_4000_sustained.rou.xml`
- run id: `global_reality4000_mild_theta_high_threshold_smoke_20260605`

결과:

- B004 reference EV travel time: 217.98초
- B04 no-control: PASS, EV travel time 3172.0초, teleport 0
- B4 high-threshold theta: PASS, EV travel time 621.0초, teleport 0
- B4 - B04 EV travel time: -2551.0초
- B04 background arrived ratio: 0.319204
- B4 background arrived ratio: 0.139273
- B04 mean delay: 458.281003초
- B4 mean delay: 171.635176초
- B4 signal event count: 268
- B4 stage2 hold: 4회, 총 17.0초
- B4 stage3 preemption: 7회
- B4 signal burden: 435.0초

## 구간 속도 분산

segment-direction 44개 기준:

- B04 평균 속도: 25.682 km/h
- B04 속도 표준편차: 26.847
- B04 최소/최대: 0.145 / 78.359 km/h
- B4 평균 속도: 35.198 km/h
- B4 속도 표준편차: 25.055
- B4 최소/최대: 0.667 / 78.488 km/h

최종 전역 현실형 신호망에서는 B4가 B04 대비 EV 통행시간을 크게 줄였고, 평균 구간 속도는 높아졌으며 속도 분산은 소폭 낮아졌다.

## 주요 산출물

- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/b04_global_reality_signal_pipeline.py`
- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/global_tls_a008_itst_mapping.csv`
- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/global_reality_signal_profiles.csv`
- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/global_reality_applied_signal_profiles.csv`
- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/global_reality4000_mild_segment_speed_summary.csv`
- `/Users/junlee/Desktop/js/09 Compact Corridor Baseline/tdata_signal/summaries/b04_global_reality_signal_summary.json`
