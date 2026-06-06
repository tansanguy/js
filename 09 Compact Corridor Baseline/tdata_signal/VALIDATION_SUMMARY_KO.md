# T-Data 전역 신호망 강화 검증 요약

작성 기준: 2026-06-05

## 범위

이 신호망은 서울 T-Data V2X 신호제어기 잔여시간 API 샘플을 최대한 사용한 `API-informed plausible global signal network`이다. 현장 신호 주기 완전 재현을 주장하지 않고, B04/B4 실험용으로 기존 임의 신호망보다 말 되는 cycle, green split, offset을 주는 것이 목적이다.

## 구현 내용

1. API를 실제 호출해 `tdata_signal/raw/tdata_spat_samples_20260605_203004.jsonl`에 5페이지 샘플을 저장했다.
2. 퇴계로 메인도로 TLS 16개는 API 기반 프로파일을 직접 배정했다.
3. 나머지 TLS 47개도 역추정이 아니라 API 기반 프로파일을 직접 배정했다.
4. 단, B04/B4 검증을 위해 메인도로 corridor는 route green 최소값과 후반 terminal 접근부 green을 보강했다.
5. 생성 net은 `tdata_signal/nets/jungbu_compact_v9_B04_tdata_plausible.net.xml`이다.
6. active B04 net `data_prepared/compact_v9/net/jungbu_compact_v9_B04_green18.net.xml`도 이 전역 API 신호망으로 덮어썼다.
7. 덮어쓰기 전 active net은 `tdata_signal/nets/jungbu_compact_v9_B04_green18.before_tdata_plausible.net.xml`에 백업했다.

## 산출물

- `tdata_plausible_signal_pipeline.py`: API 수집, 프로파일 생성, net 적용
- `tdata_signal/mainroad_signal_profiles.csv`: 메인도로 16개 API 기반 프로파일
- `tdata_signal/global_signal_profiles.csv`: 기타 TLS 47개 API 기반 프로파일
- `tdata_signal/applied_signal_profiles.csv`: 실제 net 적용 결과
- `tdata_signal/summaries/tdata_plausible_signal_summary.json`: 생성 요약

## 구조 검증

active net 기준:

- SUMO load: PASS
- TLS integrity: PASS
- firetruck route connectivity: PASS

## B04/B4 Smoke 결과

최종 실행 ID: `tdata_global_api_signal_corridor_priority_v2_20260605`

| mode | status | EV travel time | EV arrived | teleport | background arrived ratio |
| --- | --- | ---: | --- | --- | ---: |
| B004 | REFERENCE | 217.98s | true | false | - |
| B04 | FAIL | - | false | false | 0.380952 |
| B4 | PASS | 534.0s | true | false | 0.312596 |

B4는 전역 API 신호망에서도 EV를 통과시켰다. signal event는 620건, stage3 preemption은 5건, bottleneck mode는 3건이었다.

## 문제 분석

1. B04 no-control은 1800초 hard max까지 EV가 도착하지 못했다. 마지막 위치는 `781985787#0`, route index 50으로 회현 교차로 접근부다.
2. 같은 신호망에서 B4는 EV를 534초에 통과시켰다. 즉 네트워크 연결이나 TLS XML이 깨진 것이 아니라, 전역 API 신호망에서 no-control baseline이 회현 접근부 정체를 해소하지 못하는 문제다.
3. 전역 API 직접 적용은 기존 메인축 중심 신호망보다 배경 교차로 신호를 더 많이 현실화하지만, side/cross traffic과 corridor queue 상호작용이 커져 B04 baseline에는 더 가혹하다.
4. B4의 background arrived ratio는 B04보다 낮다. EV 우선 제어가 통과성은 확보하지만 배경차 부담을 키우는 trade-off가 남는다.
5. SUMO 실행 중 `SUMO_HOME` XML validation 경고, 일부 unused state 경고, `COMPACT_V9_FIRE_STATION_ENTRY_TLS` unsafe green 경고가 남는다. 구조 검증은 PASS이므로 이번 API 프로파일 적용 자체의 치명 오류는 아니다.

## 해석

최종 전역 API 신호망은 모든 TLS에 API 기반 프로파일을 배정했고, active B04/B4 net에 반영됐다. B04 no-control 실패는 신호망이 깨진 증거라기보다, 전역 신호망에서 회현 접근부 병목이 강하게 드러난 결과다. B4는 같은 조건에서 EV를 통과시키므로, 이 신호망은 B4 제어의 필요성과 병목 대응을 보는 검증용 신호망으로 사용할 수 있다.
