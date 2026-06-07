# For Junhyeok: 시각화 데이터 납품 요청 (시뮬레이션 → 시각화 핸드오프)

이 문서는 **시각화 팀이 모든 개선 작업을 시뮬레이션 코드를 건드리지 않고 할 수 있도록**, 시뮬레이션 팀이 어떤 데이터를 어떤 형식으로 뽑아 넘겨주면 되는지 정리한 계약서입니다.

원칙: 시각화 팀은 **이미 만들어진 데이터를 읽어 표현만** 합니다. 새 원천 데이터(파일에 없는 값)가 필요하면 그건 시뮬레이션 단계에서만 만들 수 있으므로, 그 목록을 여기서 요청합니다.

---

## 0. 한 줄 요약

지도 애니메이션 + 신호 현시 시각화를 하려면, 시뮬레이션 팀이 **best theta를 잠근 채 `--emit-fcd`와 `emit_tls_states=True`를 둘 다 켜고 B04/B4를 재실행**해서, 아래 "납품 번들"을 넘겨주면 됩니다.

```text
[시뮬 팀]  best theta 확보(FOR_TAEHOON 흐름)
        -> 그 theta로 emit-fcd + emit-tls 재실행 (B04 + B4)
        -> 납품 번들(아래 구조) 전달
[시각화 팀] 번들만 읽어서 HTML 애니메이션 생성 (시뮬 무수정)
```

---

## 1. 시각화가 소비하는 데이터 (계약 표면)

시각화에 **실제로 읽는 파일은 이게 전부**입니다. 이 목록 밖의 것은 필요 없습니다.

| 파일 | 모드 | 시각화가 읽는 내용 | 생성 조건 |
| --- | --- | --- | --- |
| `fcd.xml` | B04, B4 | step별·차량별 `time, id, lane, x(=lon)/y(=lat), speed, angle` — 응급차 궤적 + 배경차량 위치 | `--emit-fcd` |
| `tripinfo.xml` | B04, B4 | 응급차(`emergency_0`) 요약: `arrival, duration, routeLength, arrivalSpeed/Lane/Pos, waitingTime, timeLoss, rerouteNo, vaporized` | 항상 생성됨 |
| `tls_states.csv` | B04, B4 | 매 step EV 진행방향 신호색: `time, tls_id, link_index, ryg_char, state` (+ 가능하면 `lat, lon`) | **`emit_tls_states=True`** |
| `signal_events.csv` | B4 | B4 신호 제어 이벤트: `time, tls_id, link_index, action_type ...` | B4 모드면 자동 |
| `*.net.xml` | (정적) | 경로 좌표 + 신호 위치 복원용 (읽기 전용) | 이미 있는 정본 net |
| `*.rou.xml` | (정적) | 계획된 경로 edge 목록 | 이미 있는 정본 route |

> 배경차량은 `fcd.xml`에 **전부**(예: 한 run에 873대) 들어 있으므로, "렌더링 반경" 같은 건 시각화 쪽에서 알아서 조절합니다 — 별도 요청 불필요.

---

## 2. ★ 반드시 켜야 하는 플래그 / 포맷 (가장 중요)

### 2-1. 두 emit를 **둘 다** 켜야 함

```python
run_b04_task(..., emit_fcd=True, emit_tls_states=True)
run_b4_task (..., emit_fcd=True, emit_tls_states=True)
```

- `emit_fcd=True` → `fcd.xml` 생성. FCD는 자동으로 `geo=true`(x=lon, y=lat), `distance=true`, `period=1s`, `begin=600s`로 나옵니다 (`09 Compact Corridor Baseline/run_b0_b4_signal_pipeline.py:299-305`).
- `emit_tls_states=True` → `tls_states.csv` 생성. **이게 신호 현시 시각화의 핵심**입니다.

### 2-2. ⚠️ 정본 러너 2개는 신호 덤프를 안 만듭니다

| 러너 | `--emit-fcd` | `emit_tls_states` |
| --- | --- | --- |
| `09-1 .../run_b4_optimization_s1forced.py` | ✅ 있음 | ❌ **안 켬** |
| `10 Final Destination Validation/final_destination_validation.py` | ✅ 있음 | ❌ **안 켬** |
| `09 .../rerun_b4_best_theta_fcd.py` | ✅ | ✅ (둘 다 True) |

→ 정본 최적화/검증 러너에 `--emit-fcd`만 붙이면 **FCD만 나오고 신호 덤프(`tls_states.csv`)는 안 나옵니다.** 신호 시각화가 불가능해집니다. 따라서 **시각화용 데이터는 `rerun_b4_best_theta_fcd.py` 경로(또는 동등하게 `emit_tls_states=True`를 켠 재실행)로 뽑아야 합니다.**

### 2-3. `tls_states.csv`의 `lat, lon` 컬럼 (권장)

- 일부 run의 덤프엔 `lat, lon`이 있고(`time,tls_id,link_index,lat,lon,ryg_char,state`), 일부엔 없습니다(`time,tls_id,link_index,ryg_char,state`).
- **있으면 베스트.** 없어도 시각화가 net에서 좌표를 복원할 수 있지만, 그러려면 **net 파일 경로를 manifest에 반드시 넣어줘야** 합니다 (아래 4절).
- 좌표가 없는데 net 경로도 없으면 → 신호가 조용히 "근접 모드"로 폴백되어 **"가까운 신호만 켜지는" 버그**가 재발합니다.

---

## 3. 납품 번들 디렉터리 구조

시각화 시나리오 1개(= 출발지→목적지 1쌍, theta 1개)당 아래 한 묶음을 주세요.

```text
<bundle_root>/
├── B04/no_control/repeat_001/
│   ├── fcd.xml
│   ├── tripinfo.xml
│   └── tls_states.csv          # lat,lon 포함 권장
├── B4/<parameter_id>/repeat_001/
│   ├── fcd.xml
│   ├── tripinfo.xml
│   ├── tls_states.csv          # lat,lon 포함 권장
│   └── signal_events.csv
└── viz_manifest.json           # 4절 참고 (정합성 메타)
```

`<parameter_id>`는 best theta의 식별자(예: `bo_r11_001_...` 또는 `B4_MVP_DEFAULT`)입니다.

---

## 4. 정합성 메타데이터 (`viz_manifest.json`) — 가장 흔한 사고 방지

> 가장 큰 위험은 **net/demand/theta 불일치**입니다. BO를 net A에서 했는데 FCD 재실행을 net B에서 하면, 시각화 속도그래프가 최적화 결과와 **에러 없이 조용히** 달라집니다. 이를 막기 위해 번들에 "이 데이터가 어떤 입력에서 나왔는지"를 함께 넣어주세요.

```json
{
  "net_file": "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml",
  "net_sha256": "<해시>",
  "background_route": "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml",
  "route_rou": "data_prepared/compact_v9/routes/firetruck_to_seoul_station_front.rou.xml",
  "stage1_dir": "data_prepared/compact_v9/b4_stage1_s1forced",
  "method_run_id": "taehoon_s1forced_methods_n15_m50",
  "best_theta_parameter_id": "<parameter_id>",
  "best_theta": { "t_lead": 0, "delta_T_thr": 0, "G_ext": 0, "Q_ratio": 0, "tau": 0 },
  "hard_max_sim_time": 4000,
  "seed": 1,
  "emergency_depart_sec": 600,
  "destination_edge": "<목적지 edge>"
}
```

핵심은 **최적화에서 theta를 고를 때 쓴 net/demand와, 그 theta로 FCD를 뽑을 때 쓴 net/demand가 동일**해야 한다는 것입니다.

---

## 5. 실행 레시피 (best theta로 emit-fcd 재실행)

### 5-1. best theta 확보 (FOR_TAEHOON 흐름)

`09-1 .../outputs/${METHOD_RUN_ID}/all_evaluations.csv`에서 **PASS row 중 `score`가 가장 낮은 theta**가 best입니다 (FOR_TAEHOON.md 4절과 동일 기준).

### 5-2. 그 theta로 FCD + 신호 덤프 재실행

기존 도구 `09 Compact Corridor Baseline/rerun_b4_best_theta_fcd.py`가 **B04(무제어) + B4(제어)를 한 번에 `emit_fcd=True, emit_tls_states=True`로 재실행**합니다. 결과는 `runs/final/compact_v9_B4_viz/<run-id>_viz/`에 떨어집니다.

```bash
.venv/bin/python "09 Compact Corridor Baseline/rerun_b4_best_theta_fcd.py" \
  --bo-run-id <run-id> \
  --run-id <run-id>_viz \
  --hard-max-sim-time 4000
```

⚠️ **연결 필요 한 가지:** 이 스크립트는 현재 theta를 `results/metrics/compact_v9_B4_theta_bo/<run-id>/bo_loop_summary.json`(옛 BO 러너 산출)에서 읽습니다. 최신 정본 theta는 `09-1 .../outputs/<METHOD_RUN_ID>/all_evaluations.csv`에 있으므로, 둘 중 하나가 필요합니다:
- (a) `rerun_b4_best_theta_fcd.py:load_best_theta`가 `all_evaluations.csv`를 읽도록 확장, 또는
- (b) best theta 값을 직접 전달하는 옵션 추가.

이건 시뮬레이션 쪽 작업이라 시각화 팀이 손대지 않습니다. 연결만 해주시면 됩니다.

---

## 6. (선택) 향후 개선용 추가 데이터

지금 당장은 불필요하지만, 나중에 아래 시각화를 하려면 **시뮬 변경이 필요**합니다 (참고용).

| 원하는 시각화 | 필요한 시뮬 변경 |
| --- | --- |
| **네트워크 전체 93개 신호 현시** (경로 밖 포함) | 현재 dumper는 경로 신호 16개만 덤프. 전체 TLS를 덤프하도록 범위 확대 |
| **더 부드러운 애니메이션** (1초보다 촘촘) | `FCD_PERIOD_SEC = 1` → 더 작게 (단, FCD 용량 급증) |
| **차로별 대기열 길이 / 신호 잔여시간 화면 표시** | 해당 값을 step별로 csv에 추가 emit |

경로 신호 16개를 전 구간 활성화하는 것은 **이미 있는 `tls_states.csv`만으로 시각화 팀이 처리**합니다 (시뮬 변경 불필요).

---

## 7. GitHub로 결과 공유 (.gitignore 관련)

- **시각화 산출물(HTML, 수 MB)** 은 GitHub로 공유 가능합니다 → `results/html/shared/`에 두면 됩니다 (`.gitignore`에 허용 설정됨).
- **원천 데이터(`fcd.xml` 등, 파일당 수십 MB)** 는 **git에 올리지 마세요.** 예: 위 `fcd.xml`은 **44MB**입니다. GitHub는 50MB 초과 경고, 100MB 하드 제한이고 레포가 비대해집니다.
  - 데이터 핸드오프는 git이 아니라 **클라우드 드라이브 / GitHub Release / Git LFS** 같은 별도 채널로 주세요.
- 즉, **코드와 작은 HTML 결과는 git으로, 큰 raw 데이터(번들)는 git 밖으로** 공유하는 게 원칙입니다.
