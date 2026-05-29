# 실행 요약

최종 실험은 `docs/PIPELINE.md`와 `docs/FINAL_EXPERIMENT_RUNBOOK.md`를 기준으로 실행한다.

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

최종 효과 검증:

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline final_effect_validation_sim \
  --modes B00 B0 B2
```

## 주의

- `B1` 관련 Step 문서는 과거 smoke/진단 기록이다.
- 최종 결과 CSV는 pipeline별로 1개만 만든다.
- `ER_ACC_013`은 최종 검증 route set에서 제외한다.
