# ER_ACC_013 진단

`ER_ACC_013`은 B0 조건에서 emergency teleport 문제가 확인된 route다.

## 결론

- route 생성 자체보다 국소 정체와 lane 선택 불안정의 영향이 크다.
- 최종 `b0_valid_18` route set에서는 제외한다.
- 별도 네트워크 수정 없이 최종 실험 안정성을 우선한다.

## 최종 반영

`configs/final_experiment_manifest.json`의 `excluded_routes`에 `ER_ACC_013`을 유지한다.
