# Compact V9 Corridor Baseline

이 폴더는 기존 expanded V7/B3 맵이 실험 목적보다 과도하게 커진 문제를 해결하기 위해 만든 새 맵 파이프라인입니다.

목표는 서울역과 중부소방서를 두 초점으로 하는 최소 타원형 corridor 맵을 만들고, 사용자가 HTML로 맵을 accept한 뒤 수요와 B0/B3 실험으로 넘어가는 것입니다.

## 현재 구현 범위

- 서울역/소방서를 초점으로 하는 타원형 분석 영역 생성
- 기존 expanded OSM 원본을 재사용하되 최종 SUMO net은 타원 polygon 내부 edge만 유지
- 퇴계로 S1-S22 현실 CSV 매핑
- `1 edge = 1 차선수` 방식의 메인도로 차선 복구
- 퇴계로 메인도로 1차선 금지
- 전역 `3→1` 차로 급감 금지 audit
- 소방서 진입부 virtual entry TLS 후보 생성
- 소방차 서울역 전방 route 생성
- HTML 리뷰 및 accept gate 준비

기준 현실 CSV:

`/Users/junlee/Desktop/js/toegye_ro_mainstream_segments_english.csv`

## 실행

```bash
.venv/bin/python "09 Compact Corridor Baseline/step01_build_compact_map_review.py"
```

리뷰 HTML:

`/Users/junlee/Desktop/js/results/html/compact_v9_map_review.html`

맵을 accept한 뒤에는 다음 JSON을 생성해 후속 수요/B0 단계에서 gate로 사용합니다.

`/Users/junlee/Desktop/js/data_prepared/compact_v9/acceptance/compact_v9_map_acceptance.json`
