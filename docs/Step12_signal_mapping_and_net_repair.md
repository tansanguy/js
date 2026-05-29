# Step 12 신호 매핑과 네트워크 점검

이 단계는 emergency route와 SUMO 신호 linkIndex를 매핑하고, 네트워크 문제를 진단한 기록이다.

## 주요 내용

- route별 TLS와 emergency incoming/outgoing edge를 연결했다.
- linkIndex, green phase, yellow/clearance phase를 audit했다.
- `ER_ACC_013`은 최종 route set에서 제외하는 것으로 정리했다.

## 최종 산출물

- TLS audit: `data_prepared/signals/tls_phase_audit_spine_v2.csv`
- priority terminal 후보: `data_prepared/signals/priority_terminal_candidates.csv`

최종 B2 controller는 이 audit 정보를 사용해 조작 가능한 신호만 제어한다.
