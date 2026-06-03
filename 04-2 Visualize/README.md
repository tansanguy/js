# 04-2 Visualize

Expanded V7 전용 시각화 wrapper입니다.

- `04_visualize`, `04-1 Visualize`는 수정하지 않습니다.
- `04-1 Visualize/utils/animation_builder.py`의 B0 single-map HTML builder를 import해서 호출합니다.
- FCD는 04-2 내부에서 스트리밍으로 읽어 emergency trajectory와 주변 일반차량만 축약합니다.

기본 실행:

```bash
.venv/bin/python "04-2 Visualize/visualize_expanded_v7_b0_route.py"
```

메인 교통흐름 B0 시각화:

```bash
.venv/bin/python "04-2 Visualize/visualize_expanded_v7_b0_main_flow.py"
```

Conservative B0 결과를 별도 파일로 렌더링:

```bash
.venv/bin/python "04-2 Visualize/visualize_expanded_v7_b0_main_flow.py" \
  --latest-json results/metrics/expanded_v7_conservative_b0/latest.json \
  --manifest configs/expanded_v7_conservative_b0_manifest.json \
  --output-stem expanded_v7_conservative_b0_main_flow
```

기본 출력:

- `results/html/expanded_v7_b0_firetruck_route_animation.html`
- `results/html/expanded_v7_b0_firetruck_route_animation.json`
- `results/html/expanded_v7_b0_firetruck_route_map.html`
- `results/html/expanded_v7_b0_firetruck_route_index.html`
- `results/html/expanded_v7_b0_main_flow_animation.html`
- `results/html/expanded_v7_b0_main_flow_animation.json`
- `results/html/expanded_v7_b0_main_flow_index.html`
- `results/html/expanded_v7_conservative_b0_main_flow_animation.html`
- `results/html/expanded_v7_conservative_b0_main_flow_animation.json`
- `results/html/expanded_v7_conservative_b0_main_flow_index.html`
