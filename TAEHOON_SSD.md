# SSD 저장 구성 메모 (runs/ → 외장 SSD)

내장 디스크 용량 부족으로, 무거운 SUMO run 산출물(`runs/`)을 외장 SSD로 옮기고 심볼릭 링크로 연결한 작업 기록입니다. **코드는 한 줄도 바뀌지 않습니다.**

## 0. 배경

- 내장 디스크가 거의 가득 참(작업 시점 여유 ~267MB).
- 용량 주범은 `runs/` = **19GB** (SUMO 평가 로그·FCD 등). `results/`(234MB)·`data_prepared/`(1.3GB)는 작아서 내장 유지.
- 외장 SSD: `/Volumes/외장SSD_Samsung_870EVO` (466GB, 여유 충분).

## 1. 현재 구성

```text
/Users/iseclient1/sumo-simulation/runs   ->   /Volumes/외장SSD_Samsung_870EVO/sumo-simulation/runs   (심볼릭 링크)
```

- `runs/`는 더 이상 실제 폴더가 아니라 **SSD를 가리키는 바로가기(symlink)** 입니다.
- 스크립트가 `runs/...`에 저장하면 OS가 자동으로 SSD에 씁니다. 시뮬·시각화 공통 적용, **코드/CLI 변경 불필요**.

## 2. 적용 절차 (이미 수행함 — 재현/롤백용 기록)

```bash
SSD="/Volumes/외장SSD_Samsung_870EVO/sumo-simulation"
mkdir -p "$SSD"
rsync -ah runs/ "$SSD/runs/"     # SSD로 복사 (내장 공간 안 씀)
du -sh runs "$SSD/runs"          # 크기 일치 확인 (19G == 19G)
# 파일 개수도 일치 확인 후:
rm -rf runs                      # 내장 19GB 확보
ln -s "$SSD/runs" runs           # runs/ -> SSD 연결
ls -ld runs                      # 검증: 'runs -> /Volumes/...'
```

복사·검증(크기 19G=19G, 파일 11127개 일치) 후 원본을 삭제했으므로 데이터 손실 없음.

## 3. ⚠️ 주의사항

- **작업 중에는 SSD를 꽂아 두세요.** SSD를 빼면 `runs/` 바로가기가 깨져 시뮬·시각화가 데이터를 못 찾습니다(데이터를 SSD에 둔 결과로 당연함).
- 롤백하려면: `rm runs` (링크만 삭제, SSD 데이터는 유지) → 필요시 `rsync -ah "$SSD/runs/" runs/`로 내장에 되돌림.

## 4. git 처리 — push/pull로 데이터가 중복되지 않는 이유

핵심: **19GB run 데이터는 원래부터 git 추적 대상이 아닙니다** (`.gitignore`의 `runs/`). git이 추적하던 건 빈 placeholder `runs/final/.gitkeep`, `runs/mvp/.gitkeep` **2개뿐**입니다.

따라서:
- push/pull로 run 데이터가 오가지 않음 → **"본체 저장 + SSD 저장" 중복은 발생하지 않습니다.**
- 팀원은 자기 머신에서 자기 run 데이터를 로컬 생성(역시 gitignore). 서로 raw 데이터를 git으로 주고받지 않습니다.
- "내가 push하면 팀원이 데이터가 사라진 걸로 보고 다시 만들어 push → 내가 pull → 중복" 시나리오는 **일어나지 않습니다.** run 데이터가 애초에 git에 없기 때문입니다.

유일한 위험과 그 처리:
- `runs`를 **심볼릭 링크로 바꾸면 git이 그 링크를 새 파일로 인식**합니다. 이걸 커밋하면 팀원에겐 *내 SSD 경로*를 가리키는 **깨진 링크**가 됩니다.
- 그래서 `.gitignore`에 **`/runs`** 를 추가해 심볼릭 링크 자체를 무시하도록 했습니다(커밋되지 않음). 팀원의 `runs/`(실제 폴더)는 그대로 유지됩니다.
- placeholder `.gitkeep` 2개는 추적 해제(`git rm --cached`)했습니다. 빈 디렉터리 표식일 뿐이고, 러너가 `mkdir -p`로 run 디렉터리를 자동 생성하므로 무해합니다.

요약: **git에는 코드·작은 산출물만, 큰 raw 데이터(runs)는 SSD에 두고 git 밖.**

## 5. SSD를 쓰는 실행 명령 형식

`runs/`가 심볼릭 링크라 **기본값(`--run-root runs/...`)만 써도 자동으로 SSD에 저장**됩니다. 경로에 SSD를 명시하고 싶으면 절대경로로 지정합니다.

```bash
SSD="/Volumes/외장SSD_Samsung_870EVO/sumo-simulation"

python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --run-id taehoon_s1forced_methods_n1_m50_t6 \
  --methods BO "Random Search" CMA-ES \
  --bo-first --n 1 --m 50 --theta-per-round 6 --bo-initial 10 \
  --workers 6 --ei-candidate-count 600 \
  --net-file "09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml" \
  --background-route "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml" \
  --stage1-dir "data_prepared/compact_v9/b4_stage1_s1forced" \
  --hard-max-sim-time 4000 --skip-pareto --skip-noise-check \
  --run-root "$SSD/runs/compact_v9_B4_optimization_s1forced"
```

- `--run-root` (무거운 run 데이터) → SSD.
- `--output-dir` (분석 CSV·그림, 수 MB)는 **내장 기본값 유지 권장** — FOR_TAEHOON.md / FOR_JUNHYEOK.md가 그 로컬 경로를 참조하고, SSD를 빼도 결과 표/그림은 읽을 수 있어야 하기 때문입니다.

## 6. 다른 머신에서 설정할 때 (팀원/새 환경)

`.gitignore`에 `/runs`가 들어 있으므로 심볼릭 링크는 **각자 환경에서 직접** 만들어야 합니다(공유되지 않음).

```bash
# 예: 외장 SSD가 /Volumes/<내SSD>에 마운트된 경우
SSD="/Volumes/<내SSD>/sumo-simulation"
mkdir -p "$SSD/runs"
# (기존 runs/ 데이터가 있으면) rsync -ah runs/ "$SSD/runs/" && rm -rf runs
ln -s "$SSD/runs" runs
```

SSD를 안 쓰는 사람은 그냥 `runs/`를 실제 폴더로 두면 됩니다(기존과 동일, `runs/`가 gitignore라 영향 없음).

## 7. 기록: 이번에 SSD로 저장된 데이터

- 기존 이전분: `/Volumes/외장SSD_Samsung_870EVO/sumo-simulation/runs/` (19GB, 11127 files)
- 최적화 실행 `taehoon_s1forced_methods_n1_m50_t6` (n1·m50·t6, 3방법 × 300평가 = 900평가, 전부 PASS·도착):
  - 분석 결과(내장): `/Users/iseclient1/sumo-simulation/09-1 B4 Optimization S1forced/outputs/taehoon_s1forced_methods_n1_m50_t6/`
  - run 데이터(SSD, ~26GB): `/Volumes/외장SSD_Samsung_870EVO/sumo-simulation/runs/compact_v9_B4_optimization_s1forced/taehoon_s1forced_methods_n1_m50_t6{,_bo_*,_cma_es_*,_random_search_*}/`
  - best score 90.34 (`rs_r08_006_tl17_dt241_ge43_qr2_tau84`, Random Search)
