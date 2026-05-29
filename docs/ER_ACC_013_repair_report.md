# ER_ACC_013 보정 검토 보고

`ER_ACC_013`의 teleport 문제를 검토했지만, 최종 실험에서는 보정 variant를 채택하지 않는다.

## 판단

- emergency-only 조건에서는 통과 가능하다.
- B0 첨두 수요 조건에서는 국소 정체로 실패 가능성이 있다.
- 네트워크를 임의 수정하면 다른 route 비교의 일관성이 깨질 수 있다.

## 결정

- `ER_ACC_013`은 최종 검증 route set에서 제외한다.
- 기존 active net과 background demand는 유지한다.
