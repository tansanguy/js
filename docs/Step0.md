# Step 0 venv 환경 설정과 환경 확인

이 단계는 로컬 `.venv`를 만들고, SUMO 실행에 필요한 Python 패키지와 외부 SUMO 바이너리를 확인한다.

## 1. venv 생성

```bash
cd /Users/junlee/Desktop/js
bash 00_setup/setup_venv.sh
```

설치되는 Python 패키지는 `requirements.txt`에 고정한다.

- `PyYAML`
- `sumolib`
- `traci`

SUMO 실행 파일(`sumo`, `sumo-gui`, `netconvert`)은 pip 패키지가 아니라 시스템 설치가 필요하다.

## 2. venv 활성화

```bash
source .venv/bin/activate
```

활성화하지 않아도 `bash 00_setup/verify_env.sh`는 `.venv/bin/python`을 자동으로 우선 사용한다.

## 3. 환경 확인

```bash
bash 00_setup/verify_env.sh
```

## 확인 항목

- 현재 Python이 `.venv`의 Python인지 확인한다.
- Python 실행 가능 여부.
- `traci`, `sumolib`, `yaml` import 가능 여부.
- SUMO 실행 파일 접근 가능 여부.
- 프로젝트 입력 디렉터리 존재 여부.
- 로그 출력 경로 생성 가능 여부.

## 출력

- 로그: `outputs/logs/env_check.log`

최종 실험 전에는 이 단계가 통과해야 한다.

## 문제가 생겼을 때

- `Python import traci/sumolib/yaml` 실패: `bash 00_setup/setup_venv.sh`를 다시 실행한다.
- `command sumo` 실패: SUMO를 시스템에 설치하고 PATH에 추가한다.
- `SUMO_HOME` 실패: SUMO 설치 위치를 확인해 `export SUMO_HOME=/path/to/sumo/share/sumo` 형태로 지정한다. Homebrew 설치는 자동 추론될 수 있다.
- 입력 디렉터리 또는 config 파일 실패: repository가 완전히 준비되었는지 확인한다.
