# Experiment Design

## 연구 배경 요약

응급차량 출동 시 교차로 지체는 현장 도착시간에 직접 영향을 준다. 본 프로젝트는 SUMO 기반 시뮬레이션으로 긴급차량 우선신호 제어 방식의 효과와 일반차량 영향도를 비교한다.

## 분석 대상

- 공간 범위: 중부소방서 권역, 서울역 포함 분석권역.
- 시간대: 07~09 중 대표 10분.
- 사고지점: SUMO에서 도달 가능한 edge 중 고정 시나리오로 선정.
- 소방차: 1대, 시뮬레이션 1회당 출동 1회.
- 최적화 입력 경로: 중부소방서에서 서울역까지의 단일 경로.
- 최종 검증 경로: 기존에 정리한 B0-valid 경로 세트.

## 비교군

- `B00_freeflow`: 배경 차량 없이 응급차 1대만 주행하는 자유류 기준 run.
- `B0_peak_no_control`: B2와 같은 첨두시간 배경 수요 + 응급차 1대, 신호 조작 없음.
- `B2_peak_corridor_control`: B0과 같은 첨두시간 배경 수요 + 응급차 1대, 소방서-서울역 corridor priority 신호 제어 적용.

`B1 independent_priority`는 최종 비교에서 제외한다.

## 출력 지표

- `emergency_travel_time_sec`: SUMO tripinfo 기준 응급차 실제 통행시간.
- `b00_emergency_travel_time_sec`: 같은 route/repeat의 B00 응급차 자유류 통행시간.
- `A_delay_sec`: `emergency_travel_time_sec - b00_emergency_travel_time_sec`. B00은 `0.00`.
- `N_delay_sec`: 메인스트림 구간 내 일반차량 전체의 차량-edge별 `(실제 통과시간 - 자유류 통과시간)` 평균. B00은 `0.00`.
- `T_recovery_sec`: B2에서 응급차가 각 메인스트림 신호등을 지난 뒤 정상 프로그램으로 복귀할 때까지 걸린 시간의 최댓값. B00/B0은 `0.00`.
- `score_sec`: `A_delay_sec + N_delay_sec + T_recovery_sec`.

모든 시간 단위는 초(s), 소수 둘째자리로 저장한다. Bayesian Optimization은 이 코드베이스 내부에서 수행하지 않고, 결과 CSV를 외부 최적화 파트에 넘긴다.
