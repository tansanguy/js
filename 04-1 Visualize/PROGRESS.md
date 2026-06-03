# B0/B2 응급차 시각화 — 진행상황

_최종 갱신: 2026-06-02 · 위치: `04_visualize/`_

> 데이터 입출력 규격은 [FCD_DATA_SPEC.md](FCD_DATA_SPEC.md) 참조. 이 문서는 확정 사항·방향성·현황만 다룬다.

---

## 1. 목표

BO로 수렴한 **최종안 B2**와 **B0**를, 응급차가 대로 출발점→끝점을 진행하는 **지도 애니메이션**으로 나란히 비교한다. (경로 `FIRE_TO_SEOUL_STATION`, ≈2990 m)

## 2. 확정 사항

| 항목 | 결정 |
|------|------|
| 비교 대상 | **B0** vs **BO-최적 B2** (`d450 / a6 / g51 / t10`) |
| 산출물 | 지도 애니메이션 HTML (Leaflet) |
| 레이아웃 (옵션 A) | 좌우 2분할, 각 패널이 자기 응급차를 **추적 카메라**(zoom 17)로 추적 |
| 오버뷰 | 전체 경로 미니맵 + 두 응급차 위치 점 |
| 시간 동기 (D2) | 실제 경과시간(`t_rel`) 동기 |
| 배경(지류) 차량 (D4) | 추적영역 **250 m 반경 내**만 점으로 표시 |
| 신호 이벤트 (D1) | B2 신호 제어 이벤트 오버레이 (`signal_events.csv`) |
| FCD 수집 | runner `--emit-fcd` 토글(기본 off), 시각화 재실행 때만 on |

## 3. 작업 순서 (완료)

1. ✅ 데이터 계약서 — [FCD_DATA_SPEC.md](FCD_DATA_SPEC.md)
2. ✅ `utils/fcd_parser.py` (FCD 스트리밍 파싱, 응급차/배경 분리) + mock 생성기 `mock/make_mock_fcd.py`
3. ✅ `extract_emergency_fcd.py` (FCD + signal_events → 애니메이션 JSON; 배경 반경 필터, 신호이벤트를 응급차 위치에 앵커, 누적거리 route 길이 정규화)
4. ✅ `utils/animation_builder.py::build_animated_dual_map_html` (2분할 추적 + 미니맵 + 재생/슬라이더/배속 + 시간-속도 차트 + 신호 오버레이)
5. ✅ `animate_b0_b2_progress.py` 엔트리 / ☐ README 갱신
6. ✅ runner `--emit-fcd` 추가 → **실데이터 재실행·검증 완료**

## 4. 실데이터 결과 (run_id `20260602T055105_037584Z0000`)

| 모드 | 도달시간 | 평균속도 | 도착 | 비고 |
|------|---------|---------|------|------|
| B0 | 1472 s | 7.31 km/h | ✓ | 신호제어 없음 |
| B2 | 887 s | 12.14 km/h | ✓ | 신호 제어 35건 / 6개 교차로 |

→ **585초 단축(≈40%), 평균속도 +66%.** 산출물: `results/html/b0_b2_progress_animation.html` (+ `b0_b2_animation.json`)

## 5. 재현 방법

```bash
# (1) FCD 켜고 B0/B2 재실행 (B00은 스코어링용)
.venv/bin/python 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json --pipeline parameter_input_sim \
  --modes B00 B0 B2 --b2-params configs/generated/b2_viz_optimal.csv \
  --repeats 1 --workers 3 --emergency-depart 600 --timeout-steps 7200 \
  --recovery-buffer-sec 300 --output-prefix parameter_input_sim_viz --emit-fcd

# (2) 추출 + 애니메이션 (mock은 인자 없이 실행)
cd 04_visualize && python animate_b0_b2_progress.py \
  --b0-fcd <B0>/fcd.xml --b2-fcd <B2>/fcd.xml --b2-signals <B2>/signal_events.csv
```

## 6. 남은 일 / 메모

- README 갱신(작업 5 잔여)
- 브라우저 렌더 실측: 샌드박스에서 `open` 불가 → 사용자가 직접 HTML 열어 확인 필요
- 배경 점이 산만하면 `--bg-radius-m` 축소 또는 옵션 B(엣지 색상)로 단순화 가능
- FCD `distance` 속성은 odometer가 아니라 lane pos → 누적거리는 좌표 적산 후 route 길이로 정규화함(평균속도가 공식 지표와 일치)

## 관련 문서
- [FCD_DATA_SPEC.md](FCD_DATA_SPEC.md) · [README.md](README.md)
