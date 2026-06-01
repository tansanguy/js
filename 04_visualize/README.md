# 04_visualize - 시뮬레이션 결과 시각화

응급차 도달 시간 비교 및 베이지안 최적화 진행상황 시각화 모듈입니다.

## 구조

```
04_visualize/
├── config.py                      # 경로 및 상수 설정
├── extract_trajectory_data.py    # 궤적 데이터 추출
├── compare_b0_b2_trajectory.py   # B0/B2 비교 시각화
├── visualize_bo_progress.py      # BO 진행상황 시각화
└── utils/
    ├── sumo_result_loader.py     # SUMO 결과 로드
    ├── trajectory_parser.py      # 궤적 데이터 파싱
    ├── color_schemes.py          # 색상 팔레트
    └── leaflet_builder.py        # Leaflet 맵 빌더
```

## 사용 방법

### B0/B2 비교 시각화

```bash
python 04_visualize/compare_b0_b2_trajectory.py
```

결과: `results/html/b0_b2_trajectory_comparison.html`

옵션:
- `--run-id <RUN_ID>`: 특정 실험 ID 지정 (기본값: 최신 실험)
- `--output <PATH>`: 출력 파일 경로 지정

### 궤적 데이터 추출

```bash
python 04_visualize/extract_trajectory_data.py
```

결과: `results/html/trajectory_summary.json`

옵션:
- `--run-id <RUN_ID>`: 특정 실험 ID
- `--mode {B00,B0,B2}`: 특정 모드 필터링
- `--output <PATH>`: 출력 파일 경로

### BO 진행상황 시각화

```bash
python 04_visualize/visualize_bo_progress.py
```

결과: `results/html/bo_optimization_progress.html`

옵션:
- `--bo-run-id <BO_RUN_ID>`: 특정 BO 실행 ID
- `--output <PATH>`: 출력 파일 경로

## 입력 데이터

### 기본 경로
- 실험 결과: `results/metrics/parameter_input_sim/{run_id}/experiment_results.csv`
- BO 결과: `results/metrics/parameter_input_sim_bo/{run_id}/bo_summary.json`
- 최신 포인터: `results/metrics/parameter_input_sim/latest.json`

### 필수 CSV 컬럼
- mode: B00, B0, B2
- parameter_id: 파라미터 조합 ID
- repeat_id: 반복 실행 ID
- emergency_travel_time_sec: 응급차 도달 시간
- emergency_avg_speed_kmh: 응급차 평균 속도
- emergency_arrived: 도착 여부
- emergency_teleport: 텔레포트 여부
- final_status: PASS/FAIL/WARNING

## 색상 스킴

| 모드 | 색상 | 의미 |
|------|------|------|
| B00  | 회색 | 신호 비활성화 (자유류 기준) |
| B0   | 빨강 | 신호 조작 없음 (Baseline) |
| B2   | 파랑 | Corridor Priority 제어 |

## 확장

새로운 시각화를 추가하려면:

1. `utils/` 에서 필요한 데이터 로드 함수 작성
2. 새 Python 스크립트 작성 (예: `visualize_xyz.py`)
3. Leaflet 또는 Plotly로 HTML 생성
4. 결과를 `results/html/` 또는 `results/figures/` 에 저장
