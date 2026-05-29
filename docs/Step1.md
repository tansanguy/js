# Step 1 작업 공간 준비

이 단계는 원본 지도와 준비 산출물을 저장할 기본 디렉터리를 정리한다.

## 목적

- 프로젝트 디렉터리 구조를 고정한다.
- 이후 Step에서 생성되는 network, route, demand, metric 파일의 위치를 정한다.
- 원본 자료와 준비된 자료를 분리한다.

## 주요 디렉터리

- `data_raw`: 원본 또는 외부 입력 자료.
- `data_prepared`: SUMO 실행에 사용하는 전처리 산출물.
- `results`: metric, HTML, 중간 분석 결과.
- `outputs/logs`: 실행 로그.
- `runs`: SUMO run별 raw output.

최종 실험은 `configs/final_experiment_manifest.json`에 기록된 경로만 신뢰한다.
