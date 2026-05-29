# Step 4 분석 권역 검토

이 단계는 생성된 network를 지도와 HTML로 확인하는 단계다.

## 목적

- 분석 권역이 중부소방서와 서울역 방향 corridor를 포함하는지 확인한다.
- 차량 통행 edge와 신호 위치를 시각적으로 검토한다.
- 불필요하게 큰 full map 대신 reduced map을 최종 입력으로 사용할지 판단한다.

## 주요 산출물

- edge GeoJSON: `data_prepared/geojson/ellipse_passenger_edges.geojson`
- TLS GeoJSON: `data_prepared/geojson/ellipse_passenger_tls.geojson`
- review HTML: `results/html/map_review_ellipse_passenger.html`

최종 실험은 reduced active net을 사용한다.
