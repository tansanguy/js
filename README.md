# Emergency Signal SUMO Project

중부소방서 권역 SUMO 네트워크에서 응급차 출동과 신호 우선 제어 효과를 실험하는 프로젝트다.

## 목적

- 응급차 출발지는 중부소방서 인근 edge로 고정한다.
- 최종 실험 모드는 `B00`, `B0`, `B2` 세 가지다.
- `B00`은 배경 차량 없이 신호등을 비활성화한 응급차 자유류 기준 run이다.
- `B0`은 600초 warm-up 후에도 지속 투입되는 첨두시간 배경 수요에서 신호 조작이 없는 baseline이다.
- `B2`는 B0과 같은 지속 배경 수요에서 corridor priority 신호 제어를 적용한다.
- Bayesian Optimization은 이 저장소 안에서 수행하지 않는다. 외부 최적화기는 `configs/b2_parameter_sets.csv`를 생성하고, 이 저장소는 실행과 지표 저장을 담당한다.

## 핵심 파이프라인

- `parameter_input_sim`: 소방서에서 서울역까지의 고정 직선 route로 파라미터 입력용 지표를 만든다.
- `final_effect_validation_sim`: `b0_valid_18` route set에서 최종 파라미터 효과를 검증한다.

실행 진입점은 하나다.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py --manifest configs/final_experiment_manifest.json ...
```

## venv 설정

```bash
cd /Users/junlee/Desktop/js
bash 00_setup/setup_venv.sh
source .venv/bin/activate
bash 00_setup/verify_env.sh
```

`.venv`는 Python 패키지 의존성만 관리한다. SUMO 실행 파일은 시스템에 별도로 설치되어 PATH에서 보여야 한다.

## 주요 지표

- `A_delay_sec`: B0/B2 응급차 통행시간에서 같은 route/repeat의 B00 자유류 통행시간을 뺀 값.
- `N_delay_sec`: 응급차 출동 이후 관측창에서 main/corridor edge와 internal edge를 제외한 비메인 도로의 차량-edge 지연시간 평균.
- `T_recovery_sec`: B0/B2에서 emergency route의 모든 TLS 교차로 대기행렬 회복시간 중 최댓값.
- `score_sec`: `3*A_delay_sec + N_delay_sec + T_recovery_sec`.

모든 시간 단위는 초(s), 소수 둘째자리로 저장한다.

## 디렉터리

- `01_prepare`: 네트워크, 수요, route, 신호 audit 준비.
- `02_simulation`: B00/B0/B2 실행 러너.
- `03_results`: 결과 분석용 작업 영역.
- `configs`: 최종 manifest와 B2 파라미터 CSV.
- `docs`: 한국어 실행 문서와 과거 단계별 기록.

## 기본 입력

- net: `data_prepared/net/jungbu_ellipse_passenger_speed50.net.xml`
- background demand: `data_prepared/demand/background_routes_am_imputed_a17_a19_warm0p15_sustain0p05_seed002_sustained_3600.rou.xml`
- emergency routes: `data_prepared/routes/emergency_routes_spine_v2.csv`
- Seoul Station fixed route: `data_prepared/manual/seoul_station_manual_route.json`
- corridor edges: `data_prepared/routes/corridor_spine_edges.csv`
- B2 parameters: `configs/b2_parameter_sets.csv`

기본 결과는 `results/metrics/{output_prefix}/{run_id}/`에 시행별로 저장하고, SUMO 원본 로그는 `runs/final/{output_prefix}/{run_id}/...`에 저장한다.

자세한 실행 절차는 `docs/PIPELINE.md`와 `docs/FINAL_EXPERIMENT_RUNBOOK.md`를 따른다.
