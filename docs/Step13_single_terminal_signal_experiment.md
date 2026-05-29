# Step 13 단일 terminal 신호 진단

이 단계는 corridor priority terminal을 하나씩 켜 보며 신호별 영향을 분해한 진단 기록이다.

## 목적

- 각 TLS가 route에 미치는 영향을 개별적으로 확인한다.
- 조작 가능한 terminal과 skip해야 하는 terminal을 구분한다.
- B2 corridor 제어에 사용할 priority terminal 후보를 정리한다.

## 현재 위치

이 문서는 최종 성능 비교가 아니라 진단 기록이다. 최종 비교는 `B00`, `B0`, `B2` 러너에서 수행한다.

## 관련 파일

- `data_prepared/signals/priority_terminal_candidates.csv`
- `data_prepared/signals/tls_phase_audit_spine_v2.csv`
