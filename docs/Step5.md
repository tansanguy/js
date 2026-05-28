# Step 5 Reduced SUMO Map

## 목표

Step 5는 기존 `jungbu_area.net.xml`을 직접 자르지 않고, 기존 OSM 원본에서 새 실험용 reduced SUMO map을 만든다.

원본 map은 비교/백업용으로 유지한다. Step 6 이후 기본 입력은 reduced map 계열이다.

## 핵심 기준

- 입력 OSM: `data_raw/osm/jungbu_bbox.osm.xml`
- 두 초점: 중부소방서, 서울역
- 타원 폭: Step 1 `ellipse_width_m`
- netconvert vehicle class filter: `--keep-edges.by-vclass passenger`
- geo boundary filter: `--keep-edges.in-geo-boundary`
- HTML 파란 edge 기준: `!is_internal && allows_passenger === true`

파란 edge는 검증 완료 edge가 아니다. passenger 통행 가능하고 internal edge가 아닌 수동 선택 후보 edge다.

## 왜 원본 net.xml을 자르지 않는가

이미 만들어진 `.net.xml`을 사후 절단하면 junction, connection, internal edge, TLS link index가 깨질 수 있다.

Step 5는 OSM 입력에서 다시 netconvert를 수행한다. 이렇게 하면 netconvert가 남은 edge 기준으로 junction, connection, TLS를 다시 구성한다.

## 실행

먼저 dry-run으로 command와 타원 boundary를 확인한다.

```bash
cd /Users/junlee/Desktop/js
python3 01_prepare/01_map/step05_build_reduced_map.py --dry-run
```

실제 생성:

```bash
python3 01_prepare/01_map/step05_build_reduced_map.py
```

SUMO-GUI 확인:

```bash
sumo-gui -n data_prepared/net/jungbu_ellipse_passenger.net.xml
```

HTML 확인:

```text
results/html/map_review_ellipse_passenger.html
```

## 산출물

- `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- `data_prepared/net/jungbu_ellipse_passenger_audit.json`
- `data_prepared/net/jungbu_ellipse_passenger_manifest.json`
- `data_prepared/geojson/ellipse_passenger_edges.geojson`
- `data_prepared/geojson/ellipse_passenger_tls.geojson`
- `data_prepared/geojson/ellipse_passenger_geojson_audit.json`
- `results/html/map_review_ellipse_passenger.html`
- `outputs/logs/step05_build_reduced_map.log`

Step 6 이후 active map:

- net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- edge GeoJSON: `data_prepared/geojson/ellipse_passenger_edges.geojson`
- TLS GeoJSON: `data_prepared/geojson/ellipse_passenger_tls.geojson`
- review HTML: `results/html/map_review_ellipse_passenger.html`

Full map 산출물은 `archive/full_map_legacy/`에 보관하며, Step 6 이후 기본 실험 입력으로 사용하지 않는다.

## 현재 생성 결과

2026-05-27 실행 결과:

- 원본 edge 수: 21,599
- reduced edge 수: 3,021
- 원본 lane 수: 64,383
- reduced lane 수: 12,069
- 원본 TLS 수: 356
- reduced TLS 수: 93
- reduced GeoJSON edge 수: 3,021
- reduced GeoJSON TLS 수: 93
- reduced edge 감소율: 약 86.0%
- reduced lane 감소율: 약 81.3%
- reduced TLS 감소율: 약 73.9%

`ellipse_passenger_edges.geojson`의 모든 edge는 Step 4의 파란 edge 조건인 `!is_internal && allows_passenger === true`에 해당한다.

## 검증 기준

- 원본 full map은 archive에 보관
- reduced net 생성 성공
- reduced edge/lane/TLS 수가 원본보다 감소
- TLS 수가 0이 아님
- 중부소방서-서울역 주요축이 살아 있음
- passenger 가능 edge 중심으로 남음
- internal edge는 net 내부에 있을 수 있지만 수동 선택 후보에서는 제외
- reduced GeoJSON이 HTML에서 표시됨
- 출발 후보 edge와 사고 후보 edge 사이 route connectivity는 다음 단계에서 별도 검증

## 실패 시 조정

- TLS 0개: 타원 폭 확대 또는 boundary 완화
- 주요축 단절: 타원 폭 확대
- 출발 후보 edge 없음: 중부소방서 주변 buffer 추가 검토
- 사고 후보 edge 제거: 후보 edge_id 기반 fallback 검토
- polygon parsing 실패: `netconvert --help`의 `--keep-edges.in-geo-boundary` 포맷 재확인

## 하지 않는 일

- OSM 다운로드
- 원본 net.xml 수정/삭제
- route 생성
- demand 생성
- TraCI 구현
- 신호제어 구현
- SUMO 시뮬레이션 실행
- Bayesian Optimization
