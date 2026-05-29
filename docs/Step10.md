# Step 10 B0 baseline smoke

이 단계는 첨두시간 배경 수요와 응급차를 함께 실행해 B0 조건의 안정성을 점검한 기록이다.

## 목적

- 신호 조작 없는 조건에서 emergency route가 도착 가능한지 확인한다.
- background teleport와 route error를 기록한다.
- 최종 검증에서 제외할 route를 찾는다.

## 최종 반영

- `ER_ACC_013`은 B0 조건에서 문제가 있어 최종 `b0_valid_18`에서 제외한다.
- 최종 B0 값은 과거 Step10 산출물이 아니라 `B0` mode로 다시 실행해 CSV에 저장한다.

## 관련 출력

- B0 smoke summary: `results/metrics/b0_baseline_19route_smoke_summary.csv`
