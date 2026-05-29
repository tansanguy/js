# Step 0 환경 확인

이 단계는 로컬 실행 환경과 SUMO 관련 의존성을 확인한다.

## 실행

```bash
cd /Users/junlee/Desktop/js
bash 00_setup/verify_env.sh
```

## 확인 항목

- Python 실행 가능 여부.
- SUMO 실행 파일 접근 가능 여부.
- 프로젝트 입력 디렉터리 존재 여부.
- 로그 출력 경로 생성 가능 여부.

## 출력

- 로그: `outputs/logs/env_check.log`

최종 실험 전에는 이 단계가 통과해야 한다.
