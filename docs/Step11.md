# Step 11 Signal Priority Preparation

## Step 11A TLS Inventory + Phase Audit

active reduced SUMO net XML을 source-of-truth로 사용해 spine-v2 emergency route가 통과하는 TLS connection, linkIndex, phase state를 audit했다. GeoJSON은 제어 가능성 판단에 사용하지 않는다.

- active net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- emergency routes: `data_prepared/routes/emergency_routes_spine_v2.csv`
- audited TLS rows: `227`
- unique TLS count: `20`
- controllable rows: `227`
- status counts: `{'PASS': 227, 'WARNING': 0, 'FAIL': 0}`
- audit CSV: `data_prepared/signals/tls_phase_audit_spine_v2.csv`
- audit JSON: `data_prepared/signals/tls_phase_audit_summary.json`

## Step 11B Safety Placeholder

B1 controller smoke는 ER_ACC_002 단일 route에서 controller 시작, TLS 감지, 제어/skip 판단 로그를 확인하는 단계다. 아직 보행자 최소 보행시간을 완전한 코드 제약으로 구현하지 못하면 controller는 기존 SUMO phase sequence와 yellow/clearance를 유지하고, 안전하게 green phase로 유도할 수 없는 TLS를 skip으로 기록해야 한다.

## Step 11B ER_ACC_002 B1 Smoke

ER_ACC_002 단일 route에서 중앙형 closed-loop B1 controller smoke를 실행했다. 이 단계는 성능 개선이 아니라 controller 시작, TLS 감지, 개입/skip 로그 생성을 확인한다.

- final status: `PASS`
- controller started: `True`
- emergency arrived/teleport: `True` / `False`
- emergency travel time: `181.0`
- controlled TLS count: `4`
- skipped TLS count: `4`
- signal events: `results/metrics/b1_er_acc_002_signal_events.csv`

Safety placeholder: 보행자 최소 보행시간은 아직 독립 제약으로 완전 구현하지 않았다. 이번 smoke controller는 기존 SUMO phase sequence를 사용하고, yellow/clearance를 생략하지 않으며, 안전하게 처리할 수 없는 TLS는 skip한다.

