# Step 6 Route Connectivity Validation

## Active map

Step 6 이후 기본 입력은 Step 5 reduced map이다.

- net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- edge GeoJSON: `data_prepared/geojson/ellipse_passenger_edges.geojson`
- TLS GeoJSON: `data_prepared/geojson/ellipse_passenger_tls.geojson`
- review HTML: `results/html/map_review_ellipse_passenger.html`

Full map 계열은 `archive/full_map_legacy/`에 reference 용도로만 보관한다.

## Selected edge 기준

- `selected_edges.json` 검증 기준 GeoJSON은 `data_prepared/geojson/ellipse_passenger_edges.geojson`이다.
- 소방서 출발 edge는 `-381802881#2`이다.
- `accident_candidate_edges`는 모두 reduced edge GeoJSON에 존재해야 한다.
- 출발 edge와 사고 후보 edge는 모두 `allows_passenger=true`, `is_internal=false`여야 한다.
- route connectivity 검증 기준 net은 `data_prepared/net/jungbu_ellipse_passenger.net.xml`이다.
- reduced HTML에서 다운로드한 JSON의 `created_from`은 `results/html/map_review_ellipse_passenger.html`이어야 한다.
- `length_m < 10.0`인 edge는 warning으로 기록하지만 단독 실패 사유로 보지 않는다.
- route 가능한 사고 후보가 3개 이상이면 Step 6 성공 후보로 본다.

## 실행

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/02_manual_selection/step06_route_connectivity.py
```

이 스크립트는 active reduced map만 사용한다. edge ID 중 `-`로 시작하는 값이 있으므로 사고 후보 목록은 스크립트 내부 canonical JSON/리스트로 관리하고 unsafe positional CLI parsing을 쓰지 않는다.

## 산출물

- `data_prepared/routes/station_start_edge.json`
- `data_prepared/manual/selected_edges.json`
- `data_prepared/manual/selected_edges_validation.json`
- `data_prepared/manual/accident_candidate_edges.csv`
- `data_prepared/routes/route_connectivity_check.csv`
- `data_prepared/routes/reachable_accident_candidates.csv`
- `data_prepared/routes/route_connectivity_summary.json`
- `outputs/logs/step06_route_connectivity.log`

기존 `data_prepared/manual/selected_edges.json`이 있으면 overwrite 전에 `selected_edges.backup_*.json`으로 백업한다.

## 결과 판정

- `selected_edges_validation.json`은 edge 존재 여부, passenger 허용, internal edge 여부, 중복, 짧은 edge warning을 기록한다.
- `route_connectivity_summary.json`은 reachable/unreachable 후보 수와 Step 6 success candidate 여부를 기록한다.
- route가 있으면 route edge sequence, route length, route edge count, route TLS count를 기록한다.
- route가 없으면 edge별 `failure_reason`을 기록한다.
- TLS count 산출 중 connection 정보가 애매하면 route PASS/FAIL과 분리해 warning으로만 기록한다.

## 하지 않는 일

- full map을 Step 6 기본 입력으로 사용하지 않는다.
- `archive/full_map_legacy/html/map_review.html`을 active review HTML로 사용하지 않는다.
- archive 파일을 삭제하지 않는다.
- Step 6 검증 중 route, demand, TraCI, 신호제어, 시뮬레이션을 생성하거나 실행하지 않는다.
