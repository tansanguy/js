# 실험 설계

## 연구 배경

응급차량 출동 시 교차로 지체는 현장 도착시간에 직접 영향을 준다. 이 프로젝트는 SUMO 시뮬레이션으로 corridor priority 신호 제어가 응급차와 일반차에 미치는 영향을 비교한다.

## 분석 대상

- 공간 범위: 중부소방서 권역과 서울역 방향 corridor.
- 시간대: 첨두시간 대표 수요.
- 출동 차량: 시뮬레이션 1회당 응급차 1대.
- 파라미터 입력 route: **서울역 직선 고정 경로**. `FIRE_TO_SEOUL_STATION`, `straight_seoul_station_fixed`, 소방서에서 서울역 edge `619147738#0`까지의 59-edge route다.

## 비교군

- `B00_freeflow`: 배경 차량 없이 신호등을 비활성화한 응급차 자유류 기준 run.
- `B0_peak_no_control`: 600초 warm-up 후에도 지속되는 첨두시간 배경 수요, 신호 조작 없음.
- `B2_peak_corridor_control`: B0과 같은 지속 첨두시간 배경 수요, corridor priority 신호 제어 적용.

`B1`은 과거 진단용 smoke 코드로만 유지하고 최종 비교에서는 제외한다.

## 지표

- `emergency_travel_time_sec`: SUMO tripinfo 기준 응급차 실제 통행시간.
- `b00_emergency_travel_time_sec`: 같은 route/repeat의 B00 응급차 자유류 통행시간.
- `A_delay_sec`: 응급차 지연시간.
- `N_delay_sec`: 응급차 출동 이후 관측창의 전체 비메인 도로 일반차 지연시간 평균.
- `T_recovery_sec`: 소방서→서울역 첫 교차로 대기행렬 회복시간.
- `score_sec`: `3*A_delay_sec + N_delay_sec + T_recovery_sec`.
- `emergency_route_length_m`: 공식 보고용 경로 길이. 서울역 직선 고정 경로는 외부 edge 합산 `2990.17m`로 고정한다.

모든 시간 단위는 초(s), 소수 둘째자리로 저장한다.
