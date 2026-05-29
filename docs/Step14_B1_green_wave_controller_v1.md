# Step 14 B1 중앙 제어 smoke

이 단계는 과거 B1 중앙 제어 로직을 smoke test한 기록이다.

## 확인 항목

- controller가 TraCI에 연결되는지 확인했다.
- emergency 위치와 다음 TLS를 읽는지 확인했다.
- phase 전환, green 연장, restore event 기록을 확인했다.

## 현재 위치

최종 실험에서 B1은 사용하지 않는다. 이 문서는 B2 구현 전 controller 동작 확인 기록으로만 유지한다.

최종 신호 제어는 `B2` mode에서 `D_det`, `alpha`, `G_ext` 파라미터로 실행한다.
