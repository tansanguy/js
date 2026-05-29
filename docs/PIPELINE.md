# B00/B0/B2 실험 파이프라인

이 문서는 최종 실행 기준만 설명한다. 과거 Step 문서는 준비와 진단 기록이며, 최종 실험 명령은 `02_simulation/run_b0_b1_b2_experiment.py`만 사용한다.

## 1. 공통 입력

- manifest: `configs/final_experiment_manifest.json`
- net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- background demand: `data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml`
- route set: `results/metrics/b0_baseline_19route_smoke_summary.csv`에서 검증된 `b0_valid_18`
- 제외 route: `ER_ACC_013`
- B2 parameter CSV: `configs/b2_parameter_sets.csv`

`configs/b2_parameter_sets.csv` 필수 컬럼:

```csv
parameter_id,D_det,alpha,G_ext
```

실행 전 venv 준비:

```bash
bash 00_setup/setup_venv.sh
source .venv/bin/activate
bash 00_setup/verify_env.sh
```

## 2. 실험 모드

- `B00`: 배경 차량 없이 응급차 1대만 주행한다. 자유류 응급차 통행시간 기준값을 만든다.
- `B0`: 첨두시간 배경 수요와 응급차 1대를 함께 실행한다. 신호 조작은 없다.
- `B2`: B0과 같은 배경 수요와 응급차를 사용하고 corridor priority 신호 제어를 적용한다.

## 3. 파이프라인 1: `parameter_input_sim`

목적은 추후 외부 Bayesian Optimization이 사용할 입력 지표를 만드는 것이다. 현재 저장소는 최적화를 직접 수행하지 않고, CSV에 있는 B2 파라미터 조합만 실행한다.

- route: 소방서 edge `-381802881#2`에서 서울역 후보 edge `438360331#2`까지 synthetic route.
- modes: `B00`, `B0`, `B2`.
- output: `results/metrics/parameter_input_sim.csv`.
- raw run dir: `runs/final/parameter_input_sim/...`.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B0 B2 \
  --repeats 1 \
  --workers 1
```

## 4. 파이프라인 2: `final_effect_validation_sim`

목적은 최종 파라미터가 여러 목적지 route에서 효과가 있는지 검증하는 것이다.

- route set: `b0_valid_18`.
- excluded route: `ER_ACC_013`.
- modes: `B00`, `B0`, `B2`.
- output: `results/metrics/final_effect_validation_sim.csv`.
- raw run dir: `runs/final/final_effect_validation_sim/...`.

```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline final_effect_validation_sim \
  --modes B00 B0 B2 \
  --repeats 5 \
  --workers 4
```

## 5. CSV 지표 정의

핵심 컬럼:

- `pipeline,mode,parameter_id,repeat_id,route_id`
- `D_det,alpha,G_ext`
- `emergency_travel_time_sec,b00_emergency_travel_time_sec`
- `A_delay_sec,N_delay_sec,T_recovery_sec,score_sec`
- `emergency_arrived,emergency_teleport,background_vehicle_count,final_status,warning_reason,failure_reason,run_dir`

지표 정의:

- `A_delay_sec`: `emergency_travel_time_sec - b00_emergency_travel_time_sec`.
- `N_delay_sec`: 전체 네트워크 일반차량 중 main/corridor edge와 internal edge를 제외한 비메인 도로에서 차량-edge별 `(실제 체류시간 - 자유류 통과시간)` 평균.
- `T_recovery_sec`: B2에서 소방서→서울역 route의 첫 신호 교차로 접근 edge 전체 대기열 합계가 emergency 통과 후 출발 전 기준 이하로 처음 회복되는 시간.
- `score_sec`: 세 지표의 동일가중 합.

`B00`의 `A_delay_sec`, `N_delay_sec`, `T_recovery_sec`는 `0.00`이다. `B0`의 `T_recovery_sec`도 `0.00`이다.

## 6. 판정 기준

- emergency teleport는 `FAIL`.
- background/general teleport는 `WARNING`.
- route error는 `FAIL`.
- 모든 `_sec` 값은 초(s), 소수 둘째자리 문자열로 저장한다.
