# FCD 데이터 계약서 (시각화 ↔ 시뮬레이션 핸드오프)

> 목적: **B0 vs BO-최적 B2** 응급차 진행 애니메이션(`04_visualize/`)에 필요한 시뮬레이션 출력 규격을 정의한다.
> 이 문서 하나로 시뮬 담당자와 "무엇을 켜서 어떤 파일을 어디에 둘지"를 합의한다.
> 시뮬레이션 코드 변경은 **담당자 협의 후** 진행한다(현재 미적용).

---

## 0. 왜 추가 출력이 필요한가 (1줄 요약)

현재 출력(`tripinfo.xml` / `summary.xml` / `edgeData.xml`)에는 **차량의 시간별 위치·속도가 없다.**
애니메이션은 "매 초 어디에 있고 얼마나 빠른가"가 필요 → **FCD 출력**이 유일한 원천이다.

---

## 1. 시뮬 담당자가 산출해야 하는 것 (요청 범위)

### 1-1. 두 번의 런을 **한 번의 호출로 함께** 실행
B0와 B2를 같은 invocation에서 돌려야 repeat별 시드가 일치하여 **공정 비교**가 된다.
배경수요는 시드 고정 파일(`...seed002...rou.xml`)을 그대로 사용한다.

대상 파라미터(BO 최적 B2): **`D_det=450, alpha=6, G_ext=51, T_change_sec=10`**
(= `configs/generated/b2_bo_top3_reeval_20260531T073310_544058Z0000.csv` 의 첫 행 `bo_top3_01_d450_a6_g51_t10`. 단일 행 CSV로 만들어 전달해도 무방.)

### 1-2. 켜야 하는 출력 옵션 (신규)
SUMO 실행 커맨드(runner의 `cmd` 리스트, `run_b0_b1_b2_experiment.py:2951` 부근)에 아래를 추가:

| 옵션 | 값 | 이유 |
|------|-----|------|
| `--fcd-output` | `<run_dir>/fcd.xml` | 시간별 차량 상태 기록 |
| `--fcd-output.geo` | `true` | x,y(미터) 대신 **lon/lat(경위도)** 로 출력 → 지도에 바로 사용 |
| `--fcd-output.distance` | `true` | 누적 주행거리(odometer) 추가 → 진행률·시간-거리 그래프 |
| `--fcd-output.begin` | `<emergency_depart>` (예: `600`) | 응급차 출발 이후만 기록 → **파일 용량 절감** (워밍업 구간 제외) |

> 권장 추가(선택): `--fcd-output.acceleration true` (가속도). 필수는 아님.
> 차량 필터링은 하지 **않는다** — 추적 카메라 안의 배경(지류) 차량 점도 그려야 하므로 **모든 차량**이 필요하다. begin 시간창 제한으로 용량을 관리한다.

### 1-3. 이미 생성되지만 **보존**해야 하는 파일
- **`<run_dir>/signal_events.csv`** — B2 컨트롤러가 교차로별 녹색요청/연장을 기록(이미 생성됨). B2 신호 오버레이의 원천. 별도 작업 없이 런 디렉토리에 남기기만 하면 된다. (B0는 컨트롤러가 없어 미생성 — 정상)

### 1-4. 산출물 위치 (제안)
```
runs/final/parameter_input_sim_viz/<run_id>/
  B0/no_control/repeat_001/FIRE_TO_SEOUL_STATION/
    fcd.xml            ← 신규
    tripinfo.xml, summary.xml, edgeData.xml   (기존)
  B2/bo_top3_01_d450_a6_g51_t10/repeat_001/FIRE_TO_SEOUL_STATION/
    fcd.xml            ← 신규
    signal_events.csv  ← 보존
    tripinfo.xml, summary.xml, edgeData.xml   (기존)
```
실제 경로는 담당자 합의 후 확정. 시각화 추출기는 경로를 인자로 받게 만든다.

### 1-5. 참고 실행 커맨드 (FCD 옵션이 추가됐다고 가정)
```bash
python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json --pipeline parameter_input_sim \
  --modes B0 B2 \
  --b2-params <단일 최적 params CSV: d450/a6/g51/t10> \
  --repeats 1 --workers 1 \
  --emergency-depart 600 --timeout-steps 7200 --recovery-buffer-sec 300 \
  --output-prefix parameter_input_sim_viz \
  --emit-fcd            # ← runner에 추가할 신규 플래그(협의 대상)
```

---

## 2. FCD XML 형식 (시각화가 읽는 형식)

`--fcd-output.geo true` 적용 시 SUMO 1.26 출력:

```xml
<fcd-export>
  <timestep time="600.00">
    <vehicle id="emergency_FIRE_TO_SEOUL_STATION_B2_bo_top3_01_d450_a6_g51_t10_repeat_001"
             x="126.9701" y="37.5532"          <!-- geo=true면 x=lon, y=lat -->
             angle="87.3" type="b1_emergency_type"
             speed="11.8" pos="105.6" lane="-381802881#2_1"
             slope="0.0" distance="42.3"/>      <!-- distance=odometer(m) -->
    <vehicle id="bg_12345" x="126.9712" y="37.5540" angle="180.0"
             type="passenger" speed="6.2" pos="33.1" lane="..._0" distance="..."/>
    <!-- 해당 시각의 모든 차량 -->
  </timestep>
  <timestep time="601.00"> ... </timestep>
</fcd-export>
```

시각화가 사용하는 속성:

| 속성 | 의미 | 용도 |
|------|------|------|
| `timestep@time` | 시뮬 시각(s) | 애니메이션 시간축 (출발 기준 `t_rel`로 정렬) |
| `vehicle@id` | 차량 ID | 응급차/배경 구분 (응급차 ID는 `emergency_`로 시작) |
| `vehicle@x` (=lon), `@y` (=lat) | 경위도 | 지도 마커/점 위치 |
| `vehicle@speed` | 속도(m/s) | km/h로 변환(×3.6) → 마커 색·속도 차트 |
| `vehicle@angle` | 방위각(°) | 마커 진행방향 회전 |
| `vehicle@lane` | 현재 레인 | 현재 엣지 툴팁(레인 ID에서 엣지 추출) |
| `vehicle@distance` | 누적 거리(m) | 진행률·시간-거리 그래프 |
| `vehicle@type` | 차종 | 응급차/일반 점 스타일 구분(보조) |

> 응급차 ID 패턴: `emergency_<route>_<mode>_<param_id>_<repeat>`
> 예: `emergency_FIRE_TO_SEOUL_STATION_B2_bo_top3_01_d450_a6_g51_t10_repeat_001`

---

## 3. signal_events.csv 형식 (B2 신호 오버레이용)

이미 생성되는 파일. 시각화가 사용하는 주요 컬럼:

| 컬럼 | 용도 |
|------|------|
| `time` | 이벤트 시각 → 타임라인 정렬 |
| `tls_id`, `junction_id` | 어느 교차로 신호인가 (지도 위치 매핑) |
| `remaining_distance_m` | 응급차~교차로 잔여거리 |
| `phase_before`, `phase_after` | 신호 페이즈 변화 |
| `action` | `request_green` / `wait_t_change` / `extend` / `restore` 등 |
| `reason` | 사람이 읽을 설명 |
| `pass_time` | 응급차가 교차로 통과한 시각 |

> 한계: 이 파일은 **B2 컨트롤러의 '제어 이벤트'만** 담는다. 교차로의 평상시 신호색(녹/적) 전체 시계열은 없다.
> 신호등을 실제 색으로 그리려면 별도 수집(traci `getRedYellowGreenState` 매 스텝 기록)이 필요 — **이번 범위에서는 선택/제외**. 마커 + 제어 이벤트만으로 진행.

---

## 4. 시각화 측 책임 (담당자가 신경 쓸 필요 없는 부분)

아래는 `04_visualize/`에서 자체 구현한다 (시뮬 산출물을 입력으로 가공):

1. `utils/fcd_parser.py` — `fcd.xml` 파싱 → 응급차/배경 분리
2. `extract_emergency_fcd.py` — 위 입력 → 애니메이션용 단일 JSON 생성
3. `utils/leaflet_builder.py::build_animated_dual_map_html` — 좌우 2분할 추적 애니메이션 + 오버뷰 미니맵 렌더

### 4-1. (참고) 추출기가 만들 중간 JSON 스키마
시뮬 담당자 책임 아님 — 산출물 형태 공유용.
```jsonc
{
  "meta": { "route_id": "FIRE_TO_SEOUL_STATION", "route_length_m": 2990.17,
            "b2_params": {"D_det":450,"alpha":6,"G_ext":51,"T_change_sec":10} },
  "modes": {
    "B0": {
      "travel_time_sec": 0, "avg_speed_kmh": 0, "arrived": true, "teleported": false,
      "emergency": [ {"t_rel":0.0,"lat":37.5532,"lon":126.9701,"speed_kmh":42.5,
                      "angle":87.3,"dist_m":0.0,"edge":"-381802881#2"}, ... ],
      "background": [ {"t_rel":0.0,"vehicles":[{"lat":..,"lon":..,"speed_kmh":..}, ...]}, ... ],
      "route_polyline": [[lat,lon], ...]
    },
    "B2": { ...같은 구조..., "signal_events": [ {"t_rel":..,"lat":..,"lon":..,
              "action":"request_green","tls_id":"...","remaining_distance_m":105.6,
              "reason":"..."}, ... ] }
  }
}
```

---

## 5. 합의 체크리스트 (미팅용)

- [ ] B0·B2를 **한 호출**로 실행 (시드 일치) — `--modes B0 B2`, `--repeats 1`
- [ ] B2 파라미터 = `d450 / a6 / g51 / t10` 단일 행 CSV
- [ ] `--fcd-output` + `.geo true` + `.distance true` + `.begin <depart>` 추가
- [ ] 차량 필터링 없음(전체 차량) — begin 시간창으로 용량 관리
- [ ] `signal_events.csv` 런 디렉토리에 보존 (B2)
- [ ] 산출물 경로 확정 후 시각화 추출기에 전달
- [ ] (선택/보류) 신호등 평상시 색 시계열, 배경차량 가속도

---

_작성: 2026-06-01 · 대상 BO 최적 B2 = `bo_r06_04_d450_a6_g51_t10` (score 2009.76)_
