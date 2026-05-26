# Runbook

## Step 0 실행 명령어

```bash
cd /Users/junlee/Desktop/js
bash 00_setup/verify_env.sh
```

## 성공 기준

- `verify_env.py`가 모든 항목을 `PASS`로 출력한다.
- `outputs/logs/env_check.log`가 생성된다.
- 명령어 exit code가 `0`이다.

## 실패 시 확인할 것

- `python3`가 PATH에 있는지 확인한다.
- `SUMO_HOME` 환경변수가 설정되어 있는지 확인한다.
- `sumo`, `sumo-gui`, `netconvert`가 PATH에서 실행 가능한지 확인한다.
- Python에서 `traci`, `sumolib` import가 가능한지 확인한다.
- 지정된 필수 폴더와 `config` 파일이 삭제되지 않았는지 확인한다.
- 상세 실패 항목은 `outputs/logs/env_check.log`를 확인한다.

## 다음 Step에서 해야 할 일

- 중부소방서 권역 OSM/SUMO 맵 준비 방식 확정.
- SUMO 맵 생성 및 검토용 GeoJSON/HTML 생성 구현.
- 분석 edge, 사고 후보 edge, 제외 edge 수동 선택 워크플로 구현.
- 고정 사고 시나리오와 사고지점별 긴급차량 route 생성 구현.
- 일반차량 demand 생성 및 preflight 검증 구현.
