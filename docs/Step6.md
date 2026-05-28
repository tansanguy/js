# Step 6 Selected Edge Validation

## Active map

Step 6 이후 기본 입력은 Step 5 reduced map이다.

- net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- edge GeoJSON: `data_prepared/geojson/ellipse_passenger_edges.geojson`
- TLS GeoJSON: `data_prepared/geojson/ellipse_passenger_tls.geojson`
- review HTML: `results/html/map_review_ellipse_passenger.html`

Full map 계열은 `archive/full_map_legacy/`에 reference 용도로만 보관한다.

## Selected edge 기준

- `selected_edges.json` 검증 기준 GeoJSON은 `data_prepared/geojson/ellipse_passenger_edges.geojson`이다.
- `accident_candidate_edges`는 모두 reduced edge GeoJSON에 존재해야 한다.
- 각 사고 후보 edge는 `allows_passenger=true`, `is_internal=false`여야 한다.
- route connectivity 검증 기준 net은 `data_prepared/net/jungbu_ellipse_passenger.net.xml`이다.
- reduced HTML에서 다운로드한 JSON의 `created_from`은 `results/html/map_review_ellipse_passenger.html`이어야 한다.

## 하지 않는 일

- full map을 Step 6 기본 입력으로 사용하지 않는다.
- `archive/full_map_legacy/html/map_review.html`을 active review HTML로 사용하지 않는다.
- archive 파일을 삭제하지 않는다.
- Step 6 검증 중 route, demand, TraCI, 신호제어, 시뮬레이션을 생성하거나 실행하지 않는다.
