# Expanded V7에서 한 일 요약

## 1. 출발 경로 스크린샷 반영

소방차 출발 경로는 사용자가 준 스크린샷의 의도를 반영했다.

- 시작 위치: 소방서 남측/이면도로 쪽 파란 마커 위치
- 시작 edge: `420331801#1`
- 움직임: 북쪽으로 올라간 뒤 좌회전해서 퇴계로에 합류
- 퇴계로 합류 edge: `-174870621#8`
- 도착 목표: 서울역 POI 자체가 아니라 서울역 앞 도로 edge
- 목표 edge: `619147738#1`
- 최종 route: `61` edges, `3037.18m`
- route 정책: `max_toegye_direct_station_front`

반영 파일:

```text
data_prepared/expanded_v7/routes/firetruck_route_acceptance.json
data_prepared/expanded_v7/routes/firetruck_accepted_routes.csv
data_prepared/expanded_v7/routes/firetruck_to_seoul_station_front_conservative_b0.rou.xml
```

## 2. 정체상황 Recall 방법

이번 목표는 현실 교통량을 100% 맞추는 것이 아니라, 시뮬레이션이 말이 되는 정체류를 만드는 것이다.

기준 현실 데이터:

```text
/Users/junlee/Desktop/js/toegye_ro_mainstream_segments_english.csv
```

정체 recall은 다음 기준으로 봤다.

- CSV의 S1-S22 구간별 현실 속도/통과시간을 기준으로 비교
- SUMO `edgeData.xml`에서 edge별 속도와 통과량을 읽어 구간별로 집계
- 짧은 OSM edge의 speed 왜곡은 raw 값과 grouped 값을 분리해서 해석
- 최종 판단은 완벽한 edge 1:1 매칭보다 segment/grouped speed plausibility를 우선
- 비현실적 정지류: `5km/h` 미만
- 비현실적 자유류: `35km/h` 초과
- 목표 상태: 본선 구간이 대체로 `5~35km/h`의 현실적 정체류로 보이는 상태

도로망 자체가 병목을 만들지 않도록 `make_sense_fixed` net을 채택했다.

```text
data_prepared/expanded_v7/net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed_release_route_overopen_metered_release_fixed_lane_drop_fixed_plausibility_overopen_make_sense_fixed.net.xml
```

이 net은 post-audit 기준으로 다음을 통과했다.

- 구조 결함 `0`
- `3 -> 1` lane drop `0`
- `2 -> 1` lane drop `0`
- 끊긴 protected route pair `0`
- SUMO load `PASS`
- 소방차 route connectivity `PASS`

## 3. 메인도로 외 지류 교통량 가정

CSV는 퇴계로 본선 기준 현실 수요이므로, 지류/이면도로 수요는 직접 관측값이 아니라 가정으로 배정했다.

기본 방향:

- 본선 CSV 수요를 중심으로 생성
- 차량이 퇴계로 안에서만 갑자기 생기거나 사라지지 않도록 source/sink를 corridor 밖으로 확장
- 일부 차량은 주변 도로와 외곽 도로를 쓰도록 mapwide/background 수요로 분산
- 지류 교통량은 본선 흐름에서 파생되는 보조 흐름으로 둠

현재 conservative manifest의 주요 값:

- profile: `balanced_congestion_v8_stop_free_cleanup`
- sideflow ratio: `0.035`
- mapwide background ratio: `0.34`
- local validation share: `0.045`
- upbound through share: `0.10`
- downbound through share: `0.32`
- diversion share: `0.66`
- release depart gap: `8s`
- terminal sink extension: 최대 `30` edge
- free-segment feeder share: `0.14`

의미:

- 본선 교통량을 그대로 한 route에 몰지 않고 여러 route/template로 분산
- 병목 내부 edge에서 직접 출발/종료하는 수요는 줄임
- 서울역 말단부에서 차량이 바로 사라지지 않도록 하류 방향으로 더 빼줌
- 지류 수요는 현실 CSV에 없는 값이므로 report/assumption 성격으로 해석

반영 파일:

```text
data_prepared/expanded_v7/demand/background_routes_expanded_v7_reference_main_sideflow.rou.xml
data_prepared/expanded_v7/demand/background_routes_expanded_v7_reference_main_sideflow.rou.summary.json
configs/expanded_v7_conservative_b0_manifest.json
```

## 4. 기본 신호체계

B0에서는 응급차 우선 신호 제어를 쓰지 않는다.

- B0 bluelight/TLS priority: off
- B1/B2 제어 로직: 이번 V7 baseline 범위 밖
- SUMO 기본 신호망을 기반으로 하되, 명백히 비현실적인 짧은 green/병목은 V7 전용 net에서 보정

주요 보정 예:

- TLS id: `joinedS_11203052957_cluster_11203052955_11203052956_11203052960_11203052961_#11more`
- target link: `781985787#0 -> 218915135#3`
- link index: `18`
- green: `6s -> 30s`

이 보정은 응급차 우선제어가 아니라, 기본 baseline에서 특정 본선 직진 흐름이 비현실적으로 막히는 문제를 줄이기 위한 static 신호망 보정이다.

## 5. 보수적 소방차 B0

기존 aggressive 소방차는 시각적으로 앞차를 치고 가는 느낌이 있었기 때문에, B0 baseline은 보수적으로 바꿨다.

현재 profile:

```text
conservative_firetruck_b0
```

주요 설정:

- `vClass="emergency"`
- `guiShape="emergency"`
- depart time: `600s`
- `insertionChecks="none"` 사용 안 함
- forced lane guidance 비활성화
- `lcAssertive=1.0`
- `lcCooperative=0.7`
- `speedFactor=1.05`
- max speed: `60km/h`

해석:

- 소방차가 빠르게 가야 하지만, 앞차를 밀고 지나가는 baseline은 아님
- 막히면 departDelay/waitingTime을 허용
- B1/B2 효과를 비교할 때 더 보수적이고 현실적인 B0 기준이 됨

## 6. 현재 채택한 B0 baseline

현재 권장 baseline manifest:

```text
configs/expanded_v7_conservative_b0_manifest.json
```

이 manifest가 사용하는 핵심 입력:

```text
active_net: data_prepared/expanded_v7/net/...make_sense_fixed.net.xml
background_route: data_prepared/expanded_v7/demand/background_routes_expanded_v7_reference_main_sideflow.rou.xml
firetruck_route_xml: data_prepared/expanded_v7/routes/firetruck_to_seoul_station_front_conservative_b0.rou.xml
custom_routes: data_prepared/expanded_v7/routes/firetruck_accepted_routes.csv
```

최신 accepted run:

```text
run_id: 20260603T171418_439591Z0000
```

결과 요약:

- `sumo_exit_code=0`
- `route_error_count=0`
- `emergency_arrived=True`
- `emergency_teleport=False`
- collision `0`
- background teleport `0`
- remaining background vehicles `199`
- emergency travel time `625s`
- same-lane near-conflict `0`
- minimum same-lane distance `7.501m`

## 7. 시각화

기존 `04_visualize`는 직접 수정하지 않았다. Expanded V7 시각화는 `04-2 Visualize` wrapper에서 처리한다.

출력:

```text
results/html/expanded_v7_conservative_b0_main_flow_index.html
results/html/expanded_v7_conservative_b0_main_flow_animation.html
results/html/expanded_v7_conservative_b0_main_flow_animation.json
```

시각화에는 다음을 포함한다.

- 소방차 trajectory
- 일반차 background dots
- 본선 edge speed coloring
- stop/free edge flag
- 주요 route/source/sink 흐름

## 8. 중요한 해석

이번 V7 baseline은 "현실 교통량 100% 재현"이 아니라 다음을 목표로 한다.

- 도로망이 끊기지 않음
- 대로 본선에서 말이 안 되는 `3 -> 1` 급감 제거
- 차선수 recall을 시뮬레이션적으로 말이 되게 복구
- 본선 정체가 완전 정지류나 자유류로 붕괴하지 않게 조정
- 소방차가 앞차를 치고 지나가는 듯한 baseline 제거

남는 속도/정체 mismatch는 도로망 결함이라기보다 수요, 신호, boundary calibration 문제로 분리해서 봐야 한다.
