# 지도 제작 메모

이 문서는 분석 지도와 SUMO network를 만들 때 사용한 기준을 정리한다.

## 목표

- 중부소방서와 서울역 방향 corridor를 포함한다.
- passenger 차량이 통행 가능한 network를 만든다.
- 신호 교차로와 connection 정보를 유지한다.
- 너무 큰 full map 대신 반복 실험이 가능한 reduced map을 사용한다.

## 최종 지도

```text
data_prepared/net/jungbu_ellipse_passenger.net.xml
```

## 참고 산출물

- `data_prepared/geojson/ellipse_passenger_edges.geojson`
- `data_prepared/geojson/ellipse_passenger_tls.geojson`
- `results/html/map_review_ellipse_passenger.html`

full map 관련 자료는 `archive/full_map_legacy/`에 보관한다.
