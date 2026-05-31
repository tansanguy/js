# 실행 요약

최종 실험은 `docs/PIPELINE.md`와 `docs/FINAL_EXPERIMENT_RUNBOOK.md`를 기준으로 실행한다.

## 명령어 옵션 빠른 설명

| 옵션 | 쉬운 뜻 |
| --- | --- |
| `--manifest` | 최종 실험 설정 파일을 불러온다. |
| `--pipeline` | 결과 저장 이름이다. 현재 최종 기준은 `parameter_input_sim`이다. |
| `--modes` | 실행할 모드다. `B00` 자유류, `B0` baseline, `B2` priority 제어다. |
| `--b2-params` | B2 파라미터 CSV를 지정한다. 생략하면 기본 `configs/b2_parameter_sets.csv`를 쓴다. |
| `--repeats` | 반복 실행 횟수다. |
| `--workers` | 병렬 worker 수다. |
| `--emergency-depart` | 응급차 출동 시각이다. 기본 기준은 600초다. |

결과 리뷰는 `docs/RESULT_REVIEW_GUIDE.md`를 따른다.

## 환경 설정

```bash
cd /Users/junlee/Desktop/js
bash 00_setup/setup_venv.sh
source .venv/bin/activate
bash 00_setup/verify_env.sh
```

## 최종 실행

파라미터 입력 실험:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B0 B2
```

최종 B0/B2 비교도 같은 러너와 같은 서울역 직선 고정 경로를 사용한다. 반복 수만 `--repeats 3` 이상으로 늘린다.

## 주의

- `B1` 관련 Step 문서는 과거 smoke/진단 기록이다.
- 최종 결과 CSV는 `results/metrics/{output_prefix}/{run_id}/` 아래에 시행별로 저장한다.
- `ER_ACC_013`은 최종 검증 route set에서 제외한다.
