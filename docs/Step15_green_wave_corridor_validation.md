# Step 15 corridor 제어 검증 smoke

이 단계는 corridor 범위의 신호 제어가 route 단위로 동작하는지 확인한 기록이다.

## 목적

- corridor terminal 후보가 실제 route에서 로딩되는지 확인한다.
- route와 관련된 TLS만 제어되는지 확인한다.
- emergency teleport와 route error가 없는지 확인한다.

## 현재 위치

이 문서는 과거 smoke 결과다. 최종 실험은 `B2_peak_corridor_control` mode에서 수행하며, 최종 CSV에는 `B00`, `B0`, `B2` 결과가 함께 저장된다.
