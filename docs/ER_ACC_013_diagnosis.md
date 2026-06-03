# ER_ACC_013 진단

`ER_ACC_013`은 B0 조건에서 emergency teleport 문제가 확인된 route다.

## 결론

- route 생성 자체보다 국소 정체와 lane 선택 불안정의 영향이 크다.
- 최종 `b0_valid_18` route set에서는 제외한다.
- 별도 네트워크 수정 없이 최종 실험 안정성을 우선한다.

## 최종 반영

최종 단일 경로 실험 manifest(`configs/final_experiment_manifest.json`)는 서울역 고정 경로(`FIRE_TO_SEOUL_STATION`)만 정의하므로 별도 `excluded_routes` 필드를 두지 않는다.

다중 경로 theta 검증에서는 `05_theta_check_simulation/routes/b0_valid_18_routes.csv`가 `ER_ACC_013`을 제외한 route snapshot이다. 원본 19-route 자료는 `data_prepared/routes/emergency_routes_spine_v2.csv`에 보존한다.
