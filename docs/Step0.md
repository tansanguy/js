# Step 0 환경 확인 및 프로젝트 골격

## 목표

Step 0은 긴급차량 우선신호 SUMO 프로젝트의 기본 코드베이스 골격을 만들고, 이후 단계 실행에 필요한 로컬 환경을 확인하는 단계다.

이 단계에서는 SUMO 맵 생성, OSM 다운로드, GeoJSON 변환, HTML 지도, TraCI controller, 신호제어, route 생성, demand 생성, batch simulation, Bayesian Optimization을 구현하지 않았다.

## 완료 내용

- 프로젝트 루트는 `/Users/junlee/Desktop/js`로 고정했다.
- 별도 하위 프로젝트 폴더를 만들지 않았다.
- 전체 파이프라인용 폴더 구조를 루트 바로 아래에 생성했다.
- config placeholder 파일을 생성했다.
- `00_setup/verify_env.py`로 환경 확인 항목을 구현했다.
- `00_setup/verify_env.sh`로 프로젝트 루트 기준 실행과 로그 저장을 구현했다.
- README와 기본 문서 초안을 작성했다.
- GitHub `main` 브랜치에 초기 Step 0 커밋을 push했다.

## 주요 생성 파일

- `README.md`
- `00_setup/verify_env.py`
- `00_setup/verify_env.sh`
- `config/map_config.yaml`
- `config/demand_config.yaml`
- `config/simulation_config.yaml`
- `config/control_params_default.yaml`
- `config/run_plan_mvp.csv`
- `config/run_plan_final.csv`
- `docs/PIPELINE.md`
- `docs/RUNBOOK.md`
- `docs/EXPERIMENT_DESIGN.md`

## 생성 폴더 구조

- `config/`
- `00_setup/`
- `01_prepare/01_map/`
- `01_prepare/02_manual_selection/`
- `01_prepare/03_scenarios/`
- `01_prepare/04_routes/`
- `01_prepare/05_demand/`
- `01_prepare/06_preflight/`
- `02_simulation/controllers/`
- `02_simulation/traci_helpers/`
- `03_results/`
- `common/`
- `data_raw/osm/`
- `data_raw/traffic_counts/`
- `data_prepared/net/`
- `data_prepared/geojson/`
- `data_prepared/manual/`
- `data_prepared/scenarios/`
- `data_prepared/routes/`
- `data_prepared/demand/`
- `data_prepared/preflight/`
- `runs/mvp/`
- `runs/final/`
- `results/raw/`
- `results/metrics/`
- `results/figures/`
- `results/html/`
- `results/reports/`
- `outputs/logs/`
- `outputs/debug/`
- `docs/`

## 환경 확인 항목

`00_setup/verify_env.py`는 다음을 확인한다.

- Python 버전
- 현재 프로젝트 루트 경로
- `SUMO_HOME` 환경변수
- `sumo` 실행 가능 여부
- `sumo-gui` 실행 가능 여부
- `netconvert` 실행 가능 여부
- Python `traci` import 가능 여부
- Python `sumolib` import 가능 여부
- Python `yaml` import 가능 여부
- 필수 폴더 존재 여부
- 필수 config 파일 존재 여부

## 실행 명령

```bash
cd /Users/junlee/Desktop/js
bash 00_setup/verify_env.sh
```

## 산출 로그

- `outputs/logs/env_check.log`

## 현재 검증 결과

마지막 확인 기준 전체 PASS.

- Python: `3.12.3`
- SUMO: `1.26.0`
- `sumo`: PASS
- `sumo-gui`: PASS
- `netconvert`: PASS
- `traci`: PASS
- `sumolib`: PASS
- `yaml`: PASS
- 필수 폴더: PASS
- 필수 config 파일: PASS

## 실제 수행 결과

실행한 명령:

```bash
cd /Users/junlee/Desktop/js
bash 00_setup/verify_env.sh
```

생성/갱신된 로그:

- `outputs/logs/env_check.log`

마지막 로그 기준 결과:

```text
All checks passed.
```

확인된 환경:

- Python: `3.12.3`
- SUMO_HOME: `/Library/Frameworks/EclipseSUMO.framework/Versions/1.26.0/EclipseSUMO`
- `sumo`: `Eclipse SUMO sumo 1.26.0`
- `sumo-gui`: `Eclipse SUMO GUI 1.26.0`
- `netconvert`: `Eclipse SUMO netconvert 1.26.0`
- Python `traci`: import PASS
- Python `sumolib`: import PASS
- Python `yaml`: import PASS

비고:

- Step 1부터 YAML 로드가 필요해져 `verify_env.py`에 `yaml` import 체크를 추가했다.
- PyYAML은 사용자 Python 환경에 설치되어 있다.

## GitHub 연결

- 원격 저장소: `https://github.com/tansanguy/js.git`
- 브랜치: `main`
- 초기 커밋: `Initial Step 0 SUMO project setup`

## 다음 단계 연결

Step 0은 프로젝트 실행 환경과 디렉터리/문서 골격만 준비했다. Step 1부터는 `config/map_config.yaml`을 실제 좌표 기반으로 갱신하고, 분석권역 기준 파일을 생성한다.
