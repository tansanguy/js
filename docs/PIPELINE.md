# Pipeline

## 준비 단계: `01_prepare`

입력:
- `config/map_config.yaml`
- `config/demand_config.yaml`
- `data_raw/osm/`
- `data_raw/traffic_counts/`

출력:
- `data_prepared/net/`: SUMO network
- `data_prepared/geojson/`: 지도 검토용 GeoJSON
- `data_prepared/manual/`: 사용자가 선택한 분석 edge, 사고 후보 edge, 제외 edge
- `data_prepared/scenarios/`: 고정 사고 시나리오
- `data_prepared/routes/`: 사고지점별 긴급차량 다익스트라 route
- `data_prepared/demand/`: 일반차량 수요
- `data_prepared/preflight/`: 실행 전 검증 결과

Preflight는 시뮬레이션 실행 전 입력 데이터, 경로, 신호, 수요가 정상인지 점검하는 단계다. 도달 불가능한 사고 edge, 누락 route, 잘못된 TLS 참조, 비어 있는 demand 같은 문제를 simulation 전에 걸러낸다.

Step 6 이후 active map은 Step 5 reduced map이다.

- net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- edge GeoJSON: `data_prepared/geojson/ellipse_passenger_edges.geojson`
- TLS GeoJSON: `data_prepared/geojson/ellipse_passenger_tls.geojson`
- review HTML: `results/html/map_review_ellipse_passenger.html`

Full map 계열은 `archive/full_map_legacy/`에 reference 용도로만 보관하며 기본 실험 입력으로 사용하지 않는다.

## 시뮬레이션 단계: `02_simulation`

입력:
- `config/simulation_config.yaml`
- `config/control_params_default.yaml`
- `config/run_plan_mvp.csv`
- `config/run_plan_final.csv`
- `data_prepared/` 산출물

출력:
- `runs/mvp/`: MVP 실행별 SUMO 설정과 산출물
- `runs/final/`: final 실행별 SUMO 설정과 산출물
- `results/raw/`: SUMO raw output
- `outputs/logs/`: 실행 로그
- `outputs/debug/`: 디버그 자료

이 단계는 B0 일반신호, B1 기존 독립 우선신호, B2 제안 구간협조 우선신호를 실행한다. TraCI로 응급차 위치, 다음 신호등, linkIndex, phase 상태를 읽고 신호를 제어하는 구현은 이후 Step에서 추가한다.

## 결과 단계: `03_results`

입력:
- `results/raw/`
- `runs/`
- `config/run_plan_*.csv`

출력:
- `results/metrics/`: 응급차 통행시간, 일반차 평균 지체시간, 신호조작 이벤트, score 계산용 raw metric CSV
- `results/figures/`: 그래프
- `results/html/`: 검토용 HTML
- `results/reports/`: 보고서 산출물

최종 산출물은 `simulation_result.csv`, `final_compare.csv`, BO 팀에 넘길 `parameter_id`별 score 계산용 metric이다. Bayesian Optimization 자체는 이 코드베이스에서 수행하지 않는다.

## 데이터 영역 구분

- `data_raw/`: 외부에서 받은 원천 데이터. 직접 수정하지 않는 입력 영역.
- `data_prepared/`: prepare 단계가 만든 정제 데이터와 중간 산출물.
- `runs/`: 개별 시뮬레이션 실행 단위의 설정, 로그, 산출물 묶음.
- `results/`: raw output을 분석해 만든 metric, figure, report.
- `outputs/`: 전역 로그와 디버그 파일.
