# Original Tau 병목/측정 디버그

## 결론

`S10 max fill 0.211`, `S11 max fill 0.025`는 실제 차량이 적다는 뜻으로 보면 안 된다. 현재 original Tau strict 실험의 낮은 fill 값은 다음 두 문제가 섞인 결과다.

1. 실제 EV 병목 구간이 Case B 후보 segment에서 빠져 있다.
2. 런타임 queue/L 계산이 연속 S구간 대기행렬 길이를 직접 재는 방식이 아니라, lane-set proxy의 max queue를 쓰면서 route-span queue를 과소측정한다.

따라서 기존 strict Tau sweep 결과는 “0.65~0.85가 물리적으로 둔하다”라기보다, “현재 구현된 original Tau proxy가 병목 위치와 queue span을 제대로 못 잡는다”로 해석해야 한다.

## 가설 1: Warm-up 부족 여부

warm-up 부족이 주원인은 아니다.

EV 출발 600초 시점의 B04/global mild 상태:

- loaded: 549
- inserted: 372
- running: 275
- ended/arrived: 97
- halting: 178
- meanSpeed: 3.53 m/s
- meanSpeedRelative: 0.14

즉 600초 시점에도 이미 꽤 막혀 있다. 물론 EV를 더 늦게 출발시키면 더 깔린 맵에서 출발하므로 queue/L이 더 커질 수는 있다. 하지만 이번 `0.211/0.025` 문제의 주원인은 warm-up보다 segment 매핑/측정 오류다.

## 가설 2: 특정 구간 병목

strict original Tau run에서 EV는 다음 위치에서 stuck 처리됐다.

- termination: emergency_stuck
- termination time: 862초
- EV last edge: `218773869#6`
- EV route index: 34
- EV last speed: 0.0 km/h
- stuck duration: 120초

동일 시간대 route edge laneData를 보면 실제 병목은 `218773869#6`, `218773869#7`, `218773869#5` 주변이다.

600-900초 strict run route edge 점유/대기 상위:

- `218773869#6`, route index 34
  - 780-840초 avg vehicles: 29.464
  - avg waiting vehicles proxy: 29.333
  - avg occupancy: 62.48%
- `218773869#7`, route index 35
  - 720-780초 avg vehicles: 18.224
  - avg waiting vehicles proxy: 15.333
  - avg occupancy: 55.82%
- `218773869#5`, route index 33
  - 840-862초 avg vehicles: 12.0
  - avg waiting vehicles proxy: 11.909
  - avg occupancy: 66.84%

차량이 없는 것이 아니라, 이 구간에 차량과 대기가 충분히 있다.

## 왜 기존 Tau가 못 봤나

현재 Case B 후보는 다음 세 개뿐이다.

- S7: route index 24-29
- S10: route index 37-42
- S11: route index 41-42

하지만 실제 EV stuck 위치는 route index 34, edge `218773869#6`이다. 이 위치는 S7과 S10 사이에 있다.

더 구체적으로:

- `B4_MOVEMENT_06`은 `S9:upbound`, route index 35, edge `218773869#7 -> 218773869#8`
- `B4_MOVEMENT_07`은 `S9:upbound`, route index 37, edge `218773869#9 -> 219696193#1`
- 실제 병목 `218773869#6/#7`은 `B4_MOVEMENT_06/07`의 storage/corridor lane에는 포함된다.
- 그런데 Case B CSV는 `B4_MOVEMENT_06/07`을 S10 후보로 만들면서 segment_edges를 `218773869#9 219696193#1 219696193#2 781985793#0 781985793#1 420361196`로 잡았다.
- 따라서 병목 edge `218773869#6/#7`이 Case B segment fill 계산에서 빠졌다.

즉 “S9 병목을 S10 segment fill로 판단하는” 매핑 오류가 있다.

## 측정식 문제

현재 런타임의 `estimate_lane_set_queue_from_snapshots()`는 lane-set 안에서 `queue_m_proxy = max(queue_m_values)`를 쓴다. 이 방식은 stopline 100m local queue에는 그럭저럭 맞지만, S구간 전체 queue 길이에는 맞지 않는다.

원래 정의는:

`Tau = 구간의 차량 대기행렬 길이 / 구간 전체 길이`

이 정의라면 연속 edge를 따라 queue가 얼마나 upstream으로 밀렸는지를 봐야 한다. 하지만 현재 proxy는 여러 edge에 걸친 queue를 누적하지 않고 lane별 max만 본다. 그래서 `218773869#6/#7`처럼 연속 edge에 차량이 쌓여도 segment queue length가 과소측정된다.

실제 비교:

- strict run 결과 schema의 `queue_local_fill_100m_max`: 1.0
- strict run 결과 schema의 `queue_corridor_fill_250m_max`: 0.520465
- laneData 기반 `B4_MOVEMENT_06/07` S9 proxy:
  - corridor max fill: 1.0
  - local max fill: 1.0
  - tau 0.65 hit: 6 intervals

즉 S9 병목은 존재하지만 현재 original Tau strict trigger가 쓰는 Case B segment fill이 그 병목을 놓친다.

## 다음 수정 방향

original Tau를 제대로 쓰려면 다음 순서로 고쳐야 한다.

1. `B4Movement`에 `local_storage_edges`, `corridor_storage_edges`를 저장한다.
2. Case B CSV의 S7/S10/S11 후보만 보지 말고, 모든 `mapped_S_segment` movement에 original Tau를 계산한다.
3. queue/L 계산을 lane-set max가 아니라 route order 기반 queue span으로 바꾼다.
   - downstream stopline edge에서 upstream 방향으로 연속 queued edge 길이를 누적
   - edge별 lane queue는 max lane queue 또는 occupied/halting vehicle tail로 계산
   - denominator는 해당 S구간 실제 길이 또는 movement corridor length
4. 특히 `S9:upbound` / `B4_MOVEMENT_06/07`을 Tau trigger 대상에 포함한다.
5. 그 다음 `tau=0.25~0.85` sweep을 다시 수행한다.

## 현재 해석 보정

이전 strict Tau sweep의 “0.65~0.85 전부 미반응”은 최종 결론으로 쓰면 안 된다. 더 정확한 결론은 다음이다.

- warm-up이 완전히 부족한 것은 아니다.
- 병목은 실제로 `218773869#6/#7` 주변에 강하게 생긴다.
- 현재 original Tau 구현이 그 병목을 S구간 Tau로 포착하지 못한다.
- 따라서 먼저 S구간 매핑과 queue span 계산을 고친 뒤 민감도 sweep을 다시 해야 한다.
