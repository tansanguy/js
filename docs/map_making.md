# Map Making

## 목적

이 문서는 Step 0부터 Step 4까지의 지도/도로망 생성 과정을 설명한다.

결론부터 말하면 `map_review.html`의 파란색 SUMO edge는 "검증 완료 edge"가 아니다. 파란색은 Step 3 GeoJSON 속성 기준으로 `allows_passenger=true`이고 `is_internal=false`인 일반 주행 가능 edge를 뜻한다.

## 산출물 흐름

1. Step 0: 실행 환경과 입력 설정 확인
2. Step 1: 분석권역 GeoJSON 생성
3. Step 2: OSM 데이터를 SUMO `net.xml`로 변환
4. Step 3: SUMO `net.xml`을 지도 검토용 GeoJSON으로 변환
5. Step 4: Leaflet HTML에서 분석권역, SUMO edge, TLS를 시각 검토하고 edge 선택 JSON을 만든다

주요 산출물:

- `data_prepared/geojson/analysis_area.geojson`
- `data_prepared/sumo/*.net.xml`
- `data_prepared/geojson/sumo_edges.geojson`
- `data_prepared/geojson/sumo_tls.geojson`
- `results/html/map_review.html`

## Step 1 분석권역

분석권역은 중부소방서와 서울역 주변을 기준으로 만든다.

`analysis_area.geojson`에는 다음 역할의 feature가 포함된다.

- 중부소방서 기준점
- 서울역 기준점
- 중부소방서-서울역 분석축
- 분석 타원 또는 분석 영역
- OSM 추출 bbox

이 레이어는 도로망이 의도한 공간 범위에 들어왔는지 확인하는 기준이다.

## Step 2 OSM to SUMO

Step 2는 OSM 기반 도로 데이터를 SUMO 도로망 파일인 `net.xml`로 변환한다.

신뢰 근거:

- 입력 범위가 Step 1 bbox와 연결된다.
- SUMO 표준 도구인 `netconvert` 기반 변환 흐름을 사용한다.
- 변환 결과는 SUMO node, edge, lane, junction, TLS 구조를 가진다.
- 이후 Step 3에서 좌표와 속성을 다시 GeoJSON으로 풀어 시각 검토한다.

주의할 점:

- OSM 데이터 자체가 항상 최신/완전한 도로 원장이라는 뜻은 아니다.
- SUMO 변환 과정에서 차선, turn connection, internal edge가 시뮬레이션용 구조로 재해석된다.
- 따라서 Step 4에서 사람이 지도 위에 올려 보고 최종 사용 edge를 고른다.

## Step 3 SUMO to GeoJSON

Step 3은 SUMO `net.xml`의 edge와 TLS를 Leaflet에서 볼 수 있는 GeoJSON으로 변환한다.

`sumo_edges.geojson`의 주요 속성:

- `edge_id`: SUMO edge ID
- `from_node`, `to_node`: 연결 node
- `length_m`: edge 길이
- `speed_mps`: 제한 속도 또는 SUMO 속도
- `lane_count`: lane 수
- `is_internal`: SUMO internal edge 여부
- `allows_passenger`: passenger 차량 허용 여부
- `allows_emergency_candidate`: 응급차 후보 허용 여부
- `shape_point_count`: shape 좌표 수

`sumo_tls.geojson`의 주요 속성:

- `tls_id`: traffic light system ID
- `node_id`, `junction_id`: 연결 junction/node
- `lon`, `lat`: WGS84 좌표
- `controlled_link_count`: 제어 link 수
- `phase_count`: TLS phase 수

## Step 4 지도 검토

`map_review.html`은 다음 레이어를 지도 위에 겹쳐 보여준다.

- OSM 배경지도
- 분석권역
- SUMO edge
- 신호등/TLS

파란색 edge 의미:

- 파란색은 `allows_passenger=true`이고 `is_internal=false`인 일반 주행 가능 SUMO edge다.
- 파란색은 독립 검증 완료 표시가 아니다.
- "passenger 허용 edge만" 토글은 이 파란색 edge만 보여 주는 필터다.

다른 edge 색상:

- 보라색: SUMO internal edge
- 주황색: passenger 불가 edge
- 빨강/주황 원: TLS
- 굵은 파란/빨강/검정 선: 사용자가 선택한 edge

## 왜 이 도로망을 믿을 수 있는가

이 도로망을 그대로 "현실 도로의 완전한 정답"으로 믿는 것이 아니라, 다음 이유로 Step 5 이후 분석 입력 후보로 사용할 수 있다.

1. 입력 범위가 명시되어 있다.
   - Step 1 분석권역과 bbox가 남는다.

2. 변환 과정이 재현 가능하다.
   - config와 script 기반으로 같은 입력에서 같은 산출물을 다시 만들 수 있다.

3. SUMO 표준 네트워크 구조를 사용한다.
   - edge, lane, junction, TLS가 SUMO 형식으로 만들어진다.

4. 좌표계가 지도에서 검토 가능하다.
   - Step 3에서 WGS84 GeoJSON으로 변환하고 Leaflet/OSM 배경지도 위에 올려 확인한다.

5. 속성 기반 필터가 있다.
   - `is_internal`, `allows_passenger`, `lane_count`, `speed_mps` 등을 보고 분석용 edge를 구분할 수 있다.

6. 최종 선택은 수동 검토를 거친다.
   - Step 4에서 사람이 분석 edge, 사고 후보 edge, 제외 edge를 직접 고른다.

## 남는 한계

- OSM 원본이 오래되었거나 누락된 도로를 포함할 수 있다.
- SUMO internal edge는 실제 도로라기보다 junction 내부 연결을 표현한다.
- `allows_passenger=true`는 passenger class 통행 허용을 뜻할 뿐, 현장 검증 완료를 뜻하지 않는다.
- 차선 수, 속도, turn connection은 OSM tag와 SUMO 변환 규칙 영향을 받는다.
- 교차로 신호 phase는 SUMO 변환 결과이며 현장 신호 운영과 다를 수 있다.

## 검증 체크리스트

Step 4에서 다음을 확인한다.

- 분석권역이 서울 중부소방서-서울역 주변에 맞게 표시되는가
- SUMO edge가 OSM 배경도로와 대체로 겹치는가
- `passenger 허용 edge만` 토글을 켰을 때 일반 주행 가능 edge만 남는가
- 주황색 passenger 불가 edge가 분석 대상에 잘못 들어가지 않았는가
- 보라색 internal edge가 일반 도로처럼 선택되지 않았는가
- TLS 점이 주요 교차로 주변에 표시되는가
- 선택한 edge_id가 오른쪽 패널과 다운로드 JSON에 반영되는가

## 다음 단계와 관계

Step 4의 다운로드 결과인 `selected_edges.json`은 Step 5에서 CSV로 변환하고 검증할 예정이다.

필드 의미:

- `analysis_edges`: 분석 대상으로 볼 도로 edge
- `accident_candidate_edges`: 사고 발생 후보 edge
- `excluded_edges`: 분석에서 제외할 edge

즉, SUMO 도로망 전체를 그대로 분석에 쓰는 것이 아니라 Step 4 선택 결과를 통해 Step 5 입력을 제한한다.
