# 결과 리뷰 가이드

이 문서는 실험이 끝난 뒤 어떤 파일을 열고, 각 컬럼을 어떻게 읽어야 하는지 설명한다. 처음에는 `result_score.csv`, 그 다음 `experiment_results.csv`, 마지막으로 원본 로그를 보면 된다.

## 1. 어디를 먼저 볼까

최신 실행 위치 확인:

```bash
cat results/metrics/parameter_input_sim/latest.json
```

`latest.json`에서 자주 보는 값:

- `run_id`: 이번 실행 폴더 이름.
- `results_csv`: 전체 결과표.
- `score_components_csv`: score 관련 컬럼 중심 결과표.
- `result_score_csv`: 핵심 3개 지표만 담은 가장 가벼운 표.
- `summary_json`: 실행 전체 요약.

결과 폴더 구조:

```text
results/metrics/{output_prefix}/{run_id}/
  experiment_results.csv
  score_components.csv
  result_score.csv
  experiment_summary.json

runs/final/{output_prefix}/{run_id}/
  {mode}/{parameter_id}/{repeat_id}/{route_id}/
    tripinfo.xml
    summary.xml
    edgeData.xml
    sumo_stderr.log
    signal_events.csv
    queue_recovery_by_tls.csv
```

`results/metrics/...`는 사람이 보는 요약 결과다. `runs/final/...`는 SUMO 원본 로그와 세부 진단 파일이다.

## 2. 결과 파일별 역할

`result_score.csv`:

- 보고서와 랭킹용 최소 파일.
- 컬럼은 `A_delay_sec`, `N_delay_sec`, `T_recovery_sec` 중심이다.
- 빠르게 B0/B2 성능을 비교할 때 먼저 본다.

`score_components.csv`:

- score 계산에 필요한 값과 상태를 함께 본다.
- `final_status`, `warning_reason`, `failure_reason`, 정체 유지 지표까지 포함한다.

`experiment_results.csv`:

- 가장 자세한 최종 결과표.
- route, mode, 파라미터, 응급차 통행, 일반차 지연, 정체 유지, 신호 개입, 로그 경로가 모두 들어 있다.

`experiment_summary.json`:

- 실행 전체 요약.
- 몇 개 task가 돌았는지, 어떤 mode와 parameter가 포함됐는지, 전체 PASS/WARNING/FAIL 집계가 들어 있다.

`signal_events.csv`:

- B2가 어느 신호에서 무엇을 했는지 기록한다.
- green 연장, `T_change_sec` 이후 전환, alpha hold, skip, fail 같은 event를 확인한다.

`queue_recovery_by_tls.csv`:

- 각 TLS별 대기행렬 회복 여부와 회복 시간을 본다.
- `T_recovery_sec`가 왜 그렇게 나왔는지 확인할 때 쓴다.

`sumo_stderr.log`:

- SUMO warning과 error 원본 로그.
- route error, teleport, lane connection warning, emergency stop warning을 확인한다.

## 3. 모드 의미

`B00`:

- 자유류 기준.
- 배경 차량 없음.
- 신호등 off.
- A_delay 계산의 기준 통행시간을 만든다.

`B0`:

- 배경 차량 있음.
- 신호 제어 없음.
- “막히는 상황에서 그냥 보냈을 때” 기준이다.

`B2`:

- B0와 같은 배경 수요.
- 응급차 접근 시 corridor priority 신호 제어 적용.
- B0 대비 얼마나 좋아졌는지, 일반차와 신호 부담이 얼마나 생겼는지 본다.

## 4. 핵심 score 컬럼

`A_delay_sec`:

- 응급차 지연시간.
- `B0/B2 응급차 통행시간 - B00 자유류 통행시간`.
- 알고리즘이 줄이려는 가장 중요한 값이다.

`N_delay_sec`:

- 일반차 지연시간.
- 응급차 출동 이후 관측창에서, 비메인 도로 일반차의 차량-edge별 지연 평균이다.
- 전체 네트워크 정체감이나 응급차 병목 시간을 직접 뜻하지 않는다.

`T_recovery_sec`:

- 대기행렬 회복 시간.
- 응급차가 지난 뒤, route TLS 대기열이 출발 전 기준 이하로 돌아오는 데 걸린 시간의 최댓값이다.
- “응급차가 멈춘 시간”이 아니라 “교차로 일반차 대기열이 몇 초 뒤 회복됐나”다.

`score_sec`:

- 연구 기본 score.

```text
score_sec = 3*A_delay_sec + N_delay_sec + T_recovery_sec
```

`bo_score_sec`:

- Bayesian Optimization용 score.
- `score_sec`에 신호 개입 부담을 더한다.

```text
bo_score_sec = score_sec + signal_burden_penalty_sec + failure_penalty_sec
```

`signal_burden_penalty_sec`:

- B2가 신호를 얼마나 많이/강하게 건드렸는지 반영하는 penalty.

```text
signal_burden_penalty_sec = 0.5*total_extension_delta_sec + 30*phase_switch_count
```

`failure_penalty_sec`:

- 실패 실행 penalty용 컬럼.
- 현재 기본 정책은 실패 row를 GP 학습에서 제외하므로 보통 0이다.

## 5. 응급차 관련 컬럼

`emergency_travel_time_sec`:

- 응급차가 실제로 출발해서 도착할 때까지 걸린 시간.

`b00_emergency_travel_time_sec`:

- 같은 route/repeat의 B00 자유류 통행시간.

`emergency_avg_speed_kmh`:

- 공식 route 길이 기준 평균 속도.

`emergency_route_length_m`:

- 보고서용 공식 경로 길이.
- 서울역 직선 고정 경로는 외부 edge 합산 `2990.17m`.

`emergency_route_length_source`:

- 길이 산정 방식.
- 현재 공식값은 `fixed_external_edges`.

`emergency_arrived`:

- 응급차 도착 여부.
- `False`면 실패로 본다.

`emergency_teleport`:

- 응급차 teleport 여부.
- `True`면 실패다.

`emergency_stop_warning_count`:

- emergency stop, emergency braking, collision warning 계열 경고 수.
- 0이어야 한다.

`emergency_lane_connection_warning_count`:

- 응급차 route/lane 연결 경고 수.
- 0이어야 한다.

## 6. 일반차와 정체 컬럼

`background_departed`:

- 분석 종료 시점까지 배경 차량이 몇 대 출발했는지.

`background_arrived`:

- 분석 종료 시점까지 배경 차량이 몇 대 도착했는지.

`background_remaining_count`:

- 분석 종료 시점에 아직 네트워크에 남은 배경 차량 수.
- 남아 있다고 무조건 실패는 아니다.

`background_teleported`:

- 배경 차량 teleport 수.
- 응급차 teleport와 달리 기본은 `WARNING`이다.

`network_avg_speed_kmh`:

- 전체 관측창의 time-weighted 평균 속도.

`network_avg_speed_at_analysis_end_kmh`:

- 종료 순간 평균 속도.
- stuck 잔류 차량 진단용 보조값이다. 단독 실패 기준은 아니다.

`network_speed_pre_emergency_kmh`:

- 응급차 출동 전 300초 평균 속도.

`network_speed_during_response_kmh`:

- 응급차 출동부터 도착까지 평균 속도.

`network_speed_post_recovery_kmh`:

- queue 회복 후 `recovery_buffer_sec` 동안 평균 속도.

`rolling_congestion_valid`:

- 300초 rolling 평균 속도가 관측창 동안 허용 범위 안에 있었는지.

`rolling_congestion_reason`:

- `rolling_congestion_valid=False`일 때 이유.

## 7. 신호 제어 컬럼

`D_det`:

- 응급차가 이 거리 안에 들어오면 신호 제어 후보가 된다. 단위는 m.

`alpha`:

- 응급차가 TLS를 지난 뒤 green을 조금 더 유지하려는 시간. 단위는 초.

`G_ext`:

- green extension 상한. 단위는 초.

`T_change_sec`:

- 응급차 방향이 green이 아닐 때, 바로 바꾸지 않고 기다리는 시간. 기본은 10초.

`intervention_count`:

- B2가 실제로 제어 action을 한 횟수.

`green_extension_count`:

- 이미 green인 phase를 연장한 횟수.

`green_arrived_before_t_change_extension_count`:

- 처음엔 green이 아니었지만, `T_change_sec`를 기다리는 동안 기존 phase sequence가 먼저 green에 도달해서 연장으로 처리된 횟수.

`phase_switch_count`:

- `T_change_sec` 이후 실제 phase switch를 수행한 횟수.

`total_extension_delta_sec`:

- B2가 실제로 추가한 green extension 시간 총합.

`alpha_hold_count`:

- 응급차 통과 후 alpha hold를 시도한 횟수.

`alpha_effective_extension_sec`:

- alpha가 실제로 추가 extension으로 반영된 총 시간.
- 계속 0이면 현재 조건에서는 alpha 민감도가 낮다고 볼 수 있다.

## 8. 상태 컬럼

`final_status`:

- `PASS`: 주요 실패 조건 없음.
- `PASS_WITH_REMAINING_BACKGROUND`: 응급차와 핵심 분석은 끝났지만 일반차가 남아 있음.
- `WARNING`: 결과는 생성됐지만 정체 유지, background teleport 같은 주의사항이 있음.
- `FAIL`: 응급차 미도착, teleport, route error, SUMO 오류 같은 실패 조건.

`warning_reason`:

- `WARNING` 이유.

`failure_reason`:

- `FAIL` 이유.

`analysis_end_time_sec`:

- 지표 계산을 종료한 시각.

`analysis_stop_reason`:

- 왜 종료했는지.
- 정상 종료는 보통 `emergency_arrived_queue_recovered_buffer_elapsed`다.

`timeout_reached`:

- `timeout_steps`까지 갔는지.

`sumo_exit_code`:

- SUMO 프로세스 종료 코드.
- 0이어야 정상이다.

## 9. 결과를 읽는 순서

1. `experiment_summary.json`에서 전체 `final_status`를 본다.
2. `result_score.csv`에서 `A_delay_sec`, `N_delay_sec`, `T_recovery_sec`를 비교한다.
3. `experiment_results.csv`에서 `emergency_arrived`, `emergency_teleport`, `route_error_count`를 확인한다.
4. B2라면 `intervention_count`, `phase_switch_count`, `total_extension_delta_sec`, `signal_burden_penalty_sec`를 본다.
5. `rolling_congestion_valid`와 구간별 속도로 정체가 유지됐는지 확인한다.
6. 이상하면 `signal_events.csv`, `queue_recovery_by_tls.csv`, `sumo_stderr.log`를 본다.

## 10. 자주 헷갈리는 점

`T_recovery_sec`가 작다고 응급차가 안 막힌 것은 아니다.

- `T_recovery_sec`는 일반차 대기열 회복 지표다.
- 응급차 병목은 `emergency_travel_time_sec`, `A_delay_sec`, route progress, stderr warning으로 봐야 한다.

`N_delay_sec`가 작아도 전체 네트워크가 안 막힌 것은 아니다.

- `N_delay_sec`는 비메인 도로 vehicle-edge 평균이다.
- 전체 정체 상태는 `network_*`, `rolling_congestion_*`, active vehicle count를 같이 본다.

`B2`가 더 빨리 끝나면 배경차 수 직접 비교가 조심스럽다.

- B0/B2의 `analysis_end_time_sec`가 다르면 `background_departed`, `background_arrived`, 종료 시점 차량 수는 같은 시간 horizon 비교가 아니다.

`score_sec`와 `bo_score_sec`는 목적이 다르다.

- `score_sec`는 연구 기본 점수다.
- `bo_score_sec`는 너무 큰 `G_ext`처럼 신호 부담이 큰 조합을 BO가 덜 선호하게 만든 최적화용 점수다.

`run_id`는 사람이 정하는 이름이 아니다.

- 실행 시 자동 생성되는 시간 기반 폴더명이다.
- 최신 run은 `latest.json`으로 찾는다.
