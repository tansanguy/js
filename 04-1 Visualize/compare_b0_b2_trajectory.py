#!/usr/bin/env python3
"""Compare B0 and B2 emergency vehicle trajectories."""

import argparse
import json
from pathlib import Path
from typing import Any

from config import (
    PARAMETER_INPUT_SIM_LATEST,
    PARAMETER_INPUT_SIM_DIR,
    SEOUL_STATION_ROUTE_ID,
    HTML_OUTPUT_DIR,
    MODE_COLORS,
)
from utils import (
    load_experiment_results_csv,
    filter_results_by_mode,
    extract_emergency_metrics,
)


def load_latest_run_id(latest_json_path: Path) -> str:
    """Load latest run ID from latest.json pointer."""
    if not latest_json_path.exists():
        raise FileNotFoundError(f"Latest pointer not found: {latest_json_path}")
    
    data = json.loads(latest_json_path.read_text(encoding="utf-8"))
    return data.get("run_id", "")


def build_comparison_html(
    b0_metrics: dict[str, Any],
    b2_metrics: dict[str, Any],
    output_path: Path,
) -> None:
    """
    Build comparison HTML showing B0 vs B2 metrics.
    
    Args:
        b0_metrics: B0 mode metrics
        b2_metrics: B2 mode metrics
        output_path: Output HTML file path
    """
    # Calculate improvements
    b0_time = b0_metrics.get("travel_time_sec", 0)
    b2_time = b2_metrics.get("travel_time_sec", 0)
    time_improvement = b0_time - b2_time if b0_time > 0 else 0
    time_improvement_pct = (time_improvement / b0_time * 100) if b0_time > 0 else 0
    
    b0_speed = b0_metrics.get("avg_speed_kmh", 0)
    b2_speed = b2_metrics.get("avg_speed_kmh", 0)
    speed_improvement = ((b2_speed - b0_speed) / b0_speed * 100) if b0_speed > 0 else 0
    
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>B0 vs B2 응급차 도달 시간 비교</title>
  <style>
    html, body {{ height: 100%; margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111827; }}
    body {{ background: #f3f4f6; padding: 20px; }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    h1 {{ font-size: 24px; margin: 0 0 8px; font-weight: 700; }}
    .subtitle {{ color: #6b7280; font-size: 14px; margin-bottom: 20px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }}
    .card {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 20px; }}
    .card h2 {{ margin: 0 0 16px; font-size: 16px; display: flex; align-items: center; gap: 8px; }}
    .color-swatch {{ display: inline-block; width: 16px; height: 16px; border-radius: 3px; }}
    .metric {{ margin-bottom: 12px; }}
    .metric-label {{ font-size: 12px; color: #6b7280; margin-bottom: 4px; }}
    .metric-value {{ font-size: 20px; font-weight: 700; }}
    .status {{ font-size: 12px; margin-top: 4px; }}
    .status.pass {{ color: #10b981; }}
    .status.fail {{ color: #dc2626; }}
    .comparison {{ grid-column: 1 / -1; }}
    .improvement {{ color: #10b981; font-weight: 600; }}
    .worse {{ color: #dc2626; font-weight: 600; }}
    .table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; border-bottom: 1px solid #e5e7eb; padding: 8px; }}
    th {{ font-weight: 600; color: #6b7280; }}
    @media (max-width: 768px) {{
      .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>B0 vs B2 응급차 도달 시간 비교</h1>
    <p class="subtitle">서울역 직선 고정 경로 | 배경 수요 포함</p>
    
    <div class="grid">
      <div class="card">
        <h2><span class="color-swatch" style="background: {MODE_COLORS['B0']};"></span>B0 (신호 조작 없음)</h2>
        <div class="metric">
          <div class="metric-label">도달 시간</div>
          <div class="metric-value">{b0_metrics.get('travel_time_sec', 'N/A')} 초</div>
        </div>
        <div class="metric">
          <div class="metric-label">평균 속도</div>
          <div class="metric-value">{b0_metrics.get('avg_speed_kmh', 'N/A')} km/h</div>
        </div>
        <div class="metric">
          <div class="metric-label">상태</div>
          <div class="status {'pass' if b0_metrics.get('status') == 'PASS' else 'fail'}">
            {b0_metrics.get('status', 'UNKNOWN')}
          </div>
        </div>
      </div>
      
      <div class="card">
        <h2><span class="color-swatch" style="background: {MODE_COLORS['B2']};"></span>B2 (Corridor Priority)</h2>
        <div class="metric">
          <div class="metric-label">도달 시간</div>
          <div class="metric-value">{b2_metrics.get('travel_time_sec', 'N/A')} 초</div>
        </div>
        <div class="metric">
          <div class="metric-label">평균 속도</div>
          <div class="metric-value">{b2_metrics.get('avg_speed_kmh', 'N/A')} km/h</div>
        </div>
        <div class="metric">
          <div class="metric-label">상태</div>
          <div class="status {'pass' if b2_metrics.get('status') == 'PASS' else 'fail'}">
            {b2_metrics.get('status', 'UNKNOWN')}
          </div>
        </div>
      </div>
      
      <div class="card comparison">
        <h2>개선도</h2>
        <table class="table">
          <tr>
            <td>도달 시간 단축</td>
            <td class="{'improvement' if time_improvement > 0 else 'worse'}">
              {abs(time_improvement):.1f}초 ({time_improvement_pct:+.1f}%)
            </td>
          </tr>
          <tr>
            <td>평균 속도 향상</td>
            <td class="{'improvement' if speed_improvement > 0 else 'worse'}">
              {speed_improvement:+.1f}%
            </td>
          </tr>
        </table>
      </div>
    </div>
  </div>
</body>
</html>
"""
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Compare B0 and B2 emergency vehicle trajectories"
    )
    parser.add_argument(
        "--run-id",
        help="Specific run ID (default: latest from latest.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=HTML_OUTPUT_DIR / "b0_b2_trajectory_comparison.html",
        help="Output HTML file path",
    )
    
    args = parser.parse_args()
    
    # Determine run ID
    if args.run_id:
        run_id = args.run_id
    else:
        run_id = load_latest_run_id(PARAMETER_INPUT_SIM_LATEST)
        print(f"Using latest run: {run_id}")
    
    # Load results
    results_csv = PARAMETER_INPUT_SIM_DIR / run_id / "experiment_results.csv"
    if not results_csv.exists():
        raise FileNotFoundError(f"Results CSV not found: {results_csv}")
    
    rows = load_experiment_results_csv(results_csv)
    
    # Extract B0 and B2 metrics
    b0_rows = filter_results_by_mode(rows, "B0")
    b2_rows = filter_results_by_mode(rows, "B2")
    
    if not b0_rows or not b2_rows:
        raise ValueError("B0 or B2 results not found")
    
    # Get first result for each (summary)
    b0_metrics = extract_emergency_metrics(b0_rows[0])
    b2_metrics = extract_emergency_metrics(b2_rows[0])
    
    # Build comparison HTML
    build_comparison_html(b0_metrics, b2_metrics, args.output)
    
    print(f"Wrote comparison HTML to {args.output}")
    print(f"  B0 travel time: {b0_metrics['travel_time_sec']:.2f}s")
    print(f"  B2 travel time: {b2_metrics['travel_time_sec']:.2f}s")
    print(f"  Improvement: {b0_metrics['travel_time_sec'] - b2_metrics['travel_time_sec']:.2f}s")


if __name__ == "__main__":
    main()
