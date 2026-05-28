# Emergency Signal SUMO Project

이 프로젝트는 중부소방서 권역의 SUMO 맵에서 응급차량 출동 시뮬레이션을 수행하기 위한 전체 파이프라인 코드베이스다.

## 목적

- 사고지점은 SUMO에서 도달 가능한 edge 중 고정 시나리오로 선정한다.
- 응급차 경로는 사고지점별 다익스트라 경로를 사전에 생성해 재사용한다.
- 비교군은 `B0 baseline`, `B1 independent_priority`, `B2 corridor_priority`다.
- 각 파라미터 조합에서 응급차 통행시간과 일반차 지체시간을 측정하고 score 계산에 필요한 raw metric CSV를 출력한다.
- Bayesian Optimization은 이 코드베이스 내부에서 수행하지 않는다. 결과 CSV를 외부 최적화 파트에 넘긴다.

## 전체 흐름

1. `01_prepare`: 맵, 수동 선택, 사고 시나리오, 긴급차 경로, 일반차 수요, preflight 준비.
2. `02_simulation`: B0/B1/B2 시뮬레이션과 run plan 기반 batch 실행.
3. `03_results`: SUMO raw output 수집, 지표 계산, 비교 CSV와 그래프 생성.

## Active map

Step 6 이후 기본 맵은 Step 5 reduced map이다.

- net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- edge GeoJSON: `data_prepared/geojson/ellipse_passenger_edges.geojson`
- TLS GeoJSON: `data_prepared/geojson/ellipse_passenger_tls.geojson`
- review HTML: `results/html/map_review_ellipse_passenger.html`

Full map 계열은 `archive/full_map_legacy/`에 reference 용도로 보관하며 기본 실험 입력으로 사용하지 않는다.

## Step 0 실행법

```bash
cd /Users/junlee/Desktop/js
bash 00_setup/verify_env.sh
```

실행 로그는 `outputs/logs/env_check.log`에 저장된다.

## 아직 구현하지 않은 항목

- SUMO 맵 생성
- OSM 다운로드
- SUMO 맵 GeoJSON 변환
- 실제 지도 HTML 시각화
- 분석 edge, 사고 후보 edge, 제외 edge 선택 도구
- 사고 시나리오 생성
- 사고지점별 긴급차량 route 생성
- 일반차량 수요 생성
- TraCI controller 및 신호제어
- batch simulation
- 결과 지표 계산 및 그래프 생성
- Bayesian Optimization
