# B04 현실수요 정체 재현 및 신호 보강 요약

작성 시각: 2026-06-05T21:42:00+09:00

## 구현 범위

- A008 위치 매칭 신호망의 API hit 2개는 유지했다.
- API timing이 없는 fallback 신호는 가장 가까운 API-hit 신호 timing 계열을 빌려 `nearest_TData_timing_fallback`으로 갱신했다.
- 기존 single-phase mainline-open TLS 3개를 green/yellow/red/all-red 4상 신호로 보강했다.
- `mainstream_segment_skeleton.csv`의 1시간 교통량을 4000초 실험에 맞춰 180-3900초 window에 지속 투입하는 수요를 생성했다.
- B04/B4 runner에 `--background-route`, `--hard-max-sim-time`, B4 보수 theta 옵션을 추가했다.

## 산출물

- 보강 스크립트: `09 Compact Corridor Baseline/b04_reality_congestion_pipeline.py`
- 보강 net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_location_matched_reality_repaired.net.xml`
- 보강 profile: `09 Compact Corridor Baseline/tdata_signal/reality_repaired_signal_profiles.csv`
- 적용 profile: `09 Compact Corridor Baseline/tdata_signal/reality_repaired_applied_signal_profiles.csv`
- 현실수요 route: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_reality_4000_sustained.rou.xml`
- 구간 속도 요약: `09 Compact Corridor Baseline/tdata_signal/reality4000_segment_speed_summary.csv`
- 생성 요약 JSON: `09 Compact Corridor Baseline/tdata_signal/summaries/b04_reality_congestion_summary.json`

## 신호 보강 결과

- 적용 TLS: 16개
- single-phase 보강: 3개
  - `CSV_TLS_S11_S12_TOEGYE_RO_2_GA`
  - `CSV_TLS_S14_S15_DAYS_HOTEL_FRONT`
  - `CSV_TLS_S18_S19_SAMSEON_BUILDING`
- SUMO load: PASS
- TLS integrity: PASS
- 남은 single-phase 경고: 1개, B04 mainline 적용 대상 밖의 기존 `cluster_2457731125_436856300`

## 수요 생성 결과

- demand active window: 180-3900초
- 1시간 교통량 환산 계수: 3720 / 3600 = 1.033333
- 총 차량: 3468대
- mainline: 2006대
- segment feeder: 1102대
- midcorridor: 180대
- sideflow: 180대

## 최종 smoke

실행 ID: `reality4000_conservative_b4_smoke_v2_20260605`

| mode | parameter | status | EV time | teleport | pre-EV speed | pre-EV halting | bg arrived ratio | mean delay |
| --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: |
| B04 | B4_MVP_DEFAULT | PASS | 543.0 | False | 17.678675 | 114 | 0.087082 | 127.005397 |
| B4 | B4_CONSERVATIVE_10S | PASS | 550.0 | False | 17.678675 | 114 | 0.122261 | 168.151462 |

보수 theta는 `t_lead=20`, `Q_trig=0.75`, `G_ext=10`, `ext_max=10`, `hold_max=10`이다. 기본 B4보다 과개입은 줄었지만, 이 수요에서는 아직 B04 no-control보다 EV가 7초 느리다.

## 구간 속도 분산

- B04 segment-direction 평균속도: 37.00 km/h
- B04 segment-direction 표준편차: 25.16 km/h
- B4 segment-direction 평균속도: 35.04 km/h
- B4 segment-direction 표준편차: 25.60 km/h

정체 재현은 성공했지만, 분산 감소는 아직 실패다. 병목은 S6-S9 upbound와 S16-S22 downbound에 강하게 남는다.

## 해석

이 단계는 “현실수요 기반 정체 상황을 먼저 살리는” 목적에는 부합한다. 다만 B4 제어 성능은 보수 theta로도 아직 개선이 아니라 거의 동률에 가깝다. 다음 단계는 수요를 조금 낮추는 것보다, S6-S9 upbound 병목에 대한 B4 active movement 선택과 stage3 preemption 조건을 더 보수적으로 제한하는 쪽이 맞다.
