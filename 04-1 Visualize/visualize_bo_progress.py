#!/usr/bin/env python3
"""Visualize Bayesian Optimization progress."""

import argparse
import json
from pathlib import Path
from typing import Any

from config import (
    PARAMETER_INPUT_SIM_BO_LATEST,
    PARAMETER_INPUT_SIM_BO_DIR,
    HTML_OUTPUT_DIR,
)


def load_bo_summary(bo_summary_path: Path) -> dict[str, Any]:
    """Load BO summary JSON."""
    if not bo_summary_path.exists():
        raise FileNotFoundError(f"BO summary not found: {bo_summary_path}")
    
    return json.loads(bo_summary_path.read_text(encoding="utf-8"))


def build_bo_progress_html(
    bo_summary: dict[str, Any],
    output_path: Path,
) -> None:
    """
    Build HTML showing BO optimization progress.
    
    Args:
        bo_summary: BO summary data
        output_path: Output HTML file path
    """
    bounds = bo_summary.get("bounds", {})
    current_best = bo_summary.get("current_best", {})
    
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>베이지안 최적화 진행상황</title>
  <style>
    html, body {{ height: 100%; margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #111827; }}
    body {{ background: #f3f4f6; padding: 20px; }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    h1 {{ font-size: 24px; margin: 0 0 4px; font-weight: 700; }}
    .subtitle {{ color: #6b7280; font-size: 13px; margin-bottom: 16px; }}
    .card {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
    h2 {{ font-size: 14px; margin: 0 0 12px; font-weight: 600; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ text-align: left; border-bottom: 1px solid #e5e7eb; padding: 8px; }}
    th {{ font-weight: 600; color: #6b7280; }}
    .param-value {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>베이지안 최적화 진행상황</h1>
    <p class="subtitle">Gaussian Process 모델 기반 응급차 신호 제어 파라미터 최적화</p>
    
    <div class="card">
      <h2>탐색 공간 (Search Bounds)</h2>
      <table>
        <tr>
          <th>파라미터</th>
          <th>최소값</th>
          <th>최대값</th>
        </tr>
        <tr>
          <td>D_det (검출 거리, m)</td>
          <td class="param-value">{bounds.get('D_det', [0, 0])[0]:.0f}</td>
          <td class="param-value">{bounds.get('D_det', [0, 0])[1]:.0f}</td>
        </tr>
        <tr>
          <td>alpha (대기 확장, s)</td>
          <td class="param-value">{bounds.get('alpha', [0, 0])[0]:.0f}</td>
          <td class="param-value">{bounds.get('alpha', [0, 0])[1]:.0f}</td>
        </tr>
        <tr>
          <td>G_ext (녹색 연장, s)</td>
          <td class="param-value">{bounds.get('G_ext', [0, 0])[0]:.0f}</td>
          <td class="param-value">{bounds.get('G_ext', [0, 0])[1]:.0f}</td>
        </tr>
      </table>
    </div>
    
    <div class="card">
      <h2>현재 최적값 (Current Best)</h2>
      <table>
        <tr>
          <th>파라미터</th>
          <th>값</th>
        </tr>
        <tr>
          <td>Parameter ID</td>
          <td class="param-value">{current_best.get('parameter_id', 'N/A')}</td>
        </tr>
        <tr>
          <td>D_det</td>
          <td class="param-value">{current_best.get('D_det', 'N/A')}</td>
        </tr>
        <tr>
          <td>alpha</td>
          <td class="param-value">{current_best.get('alpha', 'N/A')}</td>
        </tr>
        <tr>
          <td>G_ext</td>
          <td class="param-value">{current_best.get('G_ext', 'N/A')}</td>
        </tr>
        <tr>
          <td>Score (초)</td>
          <td class="param-value">{current_best.get('score_sec', 'N/A')}</td>
        </tr>
      </table>
    </div>
    
    <div class="card">
      <h2>모델 정보</h2>
      <table>
        <tr>
          <th>항목</th>
          <th>값</th>
        </tr>
        <tr>
          <td>GP Kernel</td>
          <td class="param-value">{bo_summary.get('gp_kernel', 'N/A')}</td>
        </tr>
        <tr>
          <td>Acquisition Function</td>
          <td class="param-value">{bo_summary.get('acquisition', 'N/A')}</td>
        </tr>
        <tr>
          <td>Exploration Parameter (xi)</td>
          <td class="param-value">{bo_summary.get('xi', 'N/A')}</td>
        </tr>
      </table>
    </div>
  </div>
</body>
</html>
"""
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Visualize Bayesian Optimization progress"
    )
    parser.add_argument(
        "--bo-run-id",
        help="Specific BO run ID (default: latest from latest.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=HTML_OUTPUT_DIR / "bo_optimization_progress.html",
        help="Output HTML file path",
    )
    
    args = parser.parse_args()
    
    # Determine BO run ID
    if args.bo_run_id:
        bo_run_id = args.bo_run_id
    else:
        if not PARAMETER_INPUT_SIM_BO_LATEST.exists():
            print("No BO results found. Run BO first.")
            return
        
        data = json.loads(PARAMETER_INPUT_SIM_BO_LATEST.read_text(encoding="utf-8"))
        bo_run_id = data.get("run_id", "")
        print(f"Using latest BO run: {bo_run_id}")
    
    # Load BO summary
    bo_summary_path = PARAMETER_INPUT_SIM_BO_DIR / bo_run_id / "bo_summary.json"
    bo_summary = load_bo_summary(bo_summary_path)
    
    # Build HTML
    build_bo_progress_html(bo_summary, args.output)
    
    print(f"Wrote BO progress HTML to {args.output}")
    print(f"  Current best score: {bo_summary.get('current_best', {}).get('score_sec', 'N/A')}s")


if __name__ == "__main__":
    main()
