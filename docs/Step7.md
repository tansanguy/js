# Step 7 emergency route 생성

이 단계는 사고 후보별 emergency route를 생성하고 spine/corridor 정보를 정리한 기록이다.

## 주요 산출물

- emergency route CSV: `data_prepared/routes/emergency_routes_spine_v2.csv`
- corridor edge CSV: `data_prepared/routes/corridor_spine_edges.csv`

## route 정책

- 각 목적지에 대해 SUMO에서 연결 가능한 route를 생성한다.
- 최종 실험은 route CSV의 고정 route를 재사용한다.
- 서울역 파라미터 입력 route는 러너가 synthetic route로 생성한다.

## corridor edge CSV

`corridor_spine_edges.csv`는 전체 edge를 담고 있으며, main/corridor edge는 `is_spine_edge=True`로 표시한다. 최종 metric에서 비메인 도로를 구분할 때 이 표시를 사용한다.
