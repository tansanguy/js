# Truth vs Presentation Contract

이 문서는 최종 HTML에서 무엇을 알고리즘/시뮬레이션 사실로 보여야 하고, 무엇을 발표용 연출로만 써야 하는지 구분합니다.

## 현재 상태

현재 10-1 HTML은 10-1 폴더 안의 presentation-owned 입력을 기준으로 만듭니다.

| 입력 | 파일 |
| --- | --- |
| 신호망 copy | `10-1_jungbu_compact_v9_B04_global_reality_s1forced_presentation.net.xml` |
| 수요 copy | `10-1_background_routes_compact_v9_B04_ad_stage23_trigger_presentation.rou.xml` |
| EV route copy | `10-1_firetruck_to_seoul_station_front.rou.xml` |
| 입력 manifest | `10-1_presentation_inputs.json` |

신호 색은 raw SUMO TLS replay가 아니라 `10-1_suitable_signal_system`입니다. 목표는 실측 신호의 완전 재현이 아니라,
발표 화면에서 차량들이 신호를 자연스럽게 지키도록 하는 것입니다.

핵심 규칙:
- B04는 일반 신호체계로 몇 개 교차로에서 정상 red/yellow/green을 만납니다.
- B4는 EV 접근 전 green window를 열되, green이 너무 길게 고정되지 않게 닫힙니다.
- green은 통행 허가이고, 방전 보장은 아닙니다. 앞 구간 포화가 있으면 `green_downstream_queue`로 EV와 일반차가 stopline 전 큐 뒤에 정지합니다.
- 일반차는 10-1 presentation demand에서 생성한 persistent 차량이며, 표시 신호 red/yellow/allred를 통과하지 않습니다.

## 반드시 진짜로 보여줘야 하는 것

아래 항목은 알고리즘과 직결되므로 연출로 만들면 안 됩니다.

| 항목 | source-of-truth | 표시 방식 |
| --- | --- | --- |
| B4 Stage2 hold 시작 | `B4/.../signal_events.csv`의 `stage=stage2`, `action=RED_HOLD` | 실제 이벤트 시간에 entry TLS/신당역 방향 유입 차단 표시 |
| B4 Stage2 release 요청 | `stage=stage2`, `action=RELEASE_REQUEST` | 실제 이벤트 시간에 clearance 대기 표시 |
| B4 Stage2 release 완료 | `stage=stage2`, `action=RELEASE` | 실제 이벤트 시간에 유입 재개 표시 |
| Stage2 판단 근거 | `Lq_merge_m`, `Q_th_merge_m`, `n_occ_runtime_veh`, `n_need_proxy_veh`, `merge_space_deficit`, `SafetyGate_result` | 숫자/상태 배지로 표시 |
| B4 Stage3 preemption/green active | `signal_events.csv`의 `stage=stage3`, `action` | 해당 TLS에 실제 알고리즘 이벤트 배지 표시 |
| 제어 대상 TLS id | `signal_events.csv.tls_id`, 9번 net TLS id | 이벤트가 발생한 실제 TLS만 강조 |
| EV 위치/속도/도착시간 | `fcd.xml`의 emergency vehicle | 실제 궤적 기반 표시 |
| B04/B4 travel time 비교 | final smoke FCD/metrics | 실제 값 표시 |

이번 smoke에서 확인된 Stage2 실제 이벤트:

| time | action | tls_id | status |
| ---: | --- | --- | --- |
| 595.0 | `RED_HOLD` | `COMPACT_V9_FIRE_STATION_ENTRY_TLS` | `hold_active` |
| 609.0 | `RELEASE_REQUEST` | `COMPACT_V9_FIRE_STATION_ENTRY_TLS` | `release_clearance_pending` |
| 612.0 | `RELEASE` | `COMPACT_V9_FIRE_STATION_ENTRY_TLS` | `released` |

따라서 "신당역쪽에서 오는 차량을 막는 흐름"은 `595.0s-612.0s` 구간의 Stage2 hold로 표시해야 합니다.

## 연출해도 되는 것

아래 항목은 발표용으로 보정해도 됩니다. 단, 알고리즘 결과라고 말하면 안 됩니다.

| 항목 | 허용되는 연출 |
| --- | --- |
| 일반 차량 | raw FCD 차량 점은 금지. 10-1 presentation demand 차량은 신호 상태를 지키는 범위에서만 표시 |
| 정체 리본 길이/두께 | Stage2 hold 또는 red 상태를 설명하기 위한 시각 강조 가능 |
| 통행 리본 색/투명도 | green/open 상태를 설명하기 위한 시각 강조 가능 |
| 차량 점 위치/개수 | red/yellow/Rall에서는 정지선 뒤 대기열, green에서는 정지선 이전 진행 흐름으로만 연출 |
| 차량 점 이동 속도 | 10-1 demand policy와 FCD profile seed를 섞어 만든 presentation speed 사용 |
| EV 뒤 흐름 | FCD profile의 behind window를 이용해 후속 흐름/대기열로 연출 |
| 신호-차량 일관성 | 표시 신호가 red/yellow/Rall이면 실제 평균 속도와 무관하게 움직이는 점을 금지 |
| EV 정지 중 green | `green_downstream_queue`로 표시하고 일반차를 자유 진행으로 표시하지 않음 |
| EV 정지 이유 문구 | 실제 속도/다음 신호/알고리즘 라벨을 바탕으로 발표용 설명 문구를 붙임 |
| 지도 위 교통 라벨 | 일반차 점의 의미가 바로 보이도록 `정지선 뒤 대기`, `일반차 진행`, `신당역 유입 차단` 라벨 표시 |
| 신호등 아이콘 크기/위치 offset | 경로를 가리지 않도록 조정 가능 |
| 카메라 이동 | 순간이동처럼 보이지 않도록 smoothing 가능 |
| close-pair 신호 정리 | 발표용 display layer에서만 collapse/delete 가능 |
| route 좌표 보간 | FCD의 시각적 떨림 완화 가능 |

## 고쳐야 할 구현 기준

1. `signal_events.csv`를 읽어 B4 event overlay를 만든다.
2. Stage2 `COMPACT_V9_FIRE_STATION_ENTRY_TLS` 위치를 9번 net에서 찾거나, stage1의 `background_inflow_lanes`로 신당역 방향 유입 차단 위치를 계산한다.
3. `595s-612s` 동안 신당역 방향 유입 차단 리본을 실제 이벤트 기반으로 표시한다.
4. Stage3 신호 강조는 synthetic EV stop/release timeline이 아니라 `signal_events.csv`의 `stage3` actions를 기준으로 표시한다.
5. 실제 TLS 현시가 필요하면 `--emit-tls-states`가 켜진 run을 만들어 `tls_states.csv`를 사용한다. 없으면 "actual signal state"라고 표기하지 않는다.
