#!/usr/bin/env python3
"""Render a self-contained BO convergence HTML for the compact_v9 B4 theta BO run.

The existing visualize_bo_progress.py is hardwired to the parameter_input_sim_bo
(B2) layout. The B4 theta BO writes to results/metrics/compact_v9_B4_theta_bo/
with bo_rounds.csv + bo_all_values.csv, so this reads those directly and emits a
dependency-free HTML (inline SVG, no JS libraries).
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
B4_BO_DIR = PROJECT_ROOT / "results/metrics/compact_v9_B4_theta_bo"
HTML_OUTPUT_DIR = PROJECT_ROOT / "results/html"

# Scores at/above this are failure penalties, not real evaluations.
PENALTY_THRESHOLD = 100000.0


def load_rounds(run_dir: Path) -> list[dict]:
    rows = list(csv.DictReader((run_dir / "bo_rounds.csv").open(encoding="utf-8")))
    out = []
    for r in rows:
        out.append({"round": int(r["round"]), "best": float(r["best_bo_score_sec"])})
    return out


def load_evals(run_dir: Path) -> list[dict]:
    rows = list(csv.DictReader((run_dir / "bo_all_values.csv").open(encoding="utf-8")))
    out = []
    for r in rows:
        try:
            rd = int(r["bo_round"])
            s = float(r["bo_score_sec"])
        except (KeyError, ValueError):
            continue
        out.append({"round": rd, "score": s, "penalty": s >= PENALTY_THRESHOLD,
                    "parameter_id": r.get("parameter_id", "")})
    return out


def load_summary(run_dir: Path) -> dict:
    p = run_dir / "bo_loop_summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def build_html(run_id: str, rounds: list[dict], evals: list[dict], summary: dict) -> str:
    # Plot geometry
    W, H = 900, 460
    ml, mr, mt, mb = 70, 30, 40, 50
    pw, ph = W - ml - mr, H - mt - mb

    valid = [e for e in evals if not e["penalty"]]
    rmin = min(r["round"] for r in rounds)
    rmax = max(r["round"] for r in rounds)
    smin = min(e["score"] for e in valid)
    smax = max(e["score"] for e in valid)
    # pad y
    pad = (smax - smin) * 0.08 or 1.0
    y0, y1 = smin - pad, smax + pad

    def px(rd: float) -> float:
        return ml + (rd - rmin) / (rmax - rmin or 1) * pw

    def py(s: float) -> float:
        return mt + (1 - (s - y0) / (y1 - y0)) * ph

    # gridlines (5 y ticks)
    yticks = []
    for i in range(6):
        val = y0 + (y1 - y0) * i / 5
        yticks.append((val, py(val)))
    xticks = [(rd, px(rd)) for rd in range(rmin, rmax + 1, max(1, (rmax - rmin) // 10))]

    # best-so-far step line
    best_pts = " ".join(f"{px(r['round']):.1f},{py(r['best']):.1f}" for r in rounds)

    # scatter dots
    dots = []
    for e in valid:
        dots.append(
            f'<circle cx="{px(e["round"]):.1f}" cy="{py(e["score"]):.1f}" r="3.2" '
            f'fill="#3b82f6" fill-opacity="0.55"><title>round {e["round"]}\n{e["parameter_id"]}\n{e["score"]:.1f}s</title></circle>'
        )
    dots_svg = "\n".join(dots)

    grid_svg = ""
    for val, yy in yticks:
        grid_svg += f'<line x1="{ml}" y1="{yy:.1f}" x2="{ml+pw}" y2="{yy:.1f}" stroke="#e5e7eb"/>'
        grid_svg += f'<text x="{ml-8}" y="{yy+4:.1f}" text-anchor="end" font-size="11" fill="#6b7280">{val:.0f}</text>'
    for rd, xx in xticks:
        grid_svg += f'<text x="{xx:.1f}" y="{mt+ph+18}" text-anchor="middle" font-size="11" fill="#6b7280">{rd}</text>'

    best = summary.get("best", {})
    n_penalty = sum(1 for e in evals if e["penalty"])
    first_best = rounds[0]["best"]
    final_best = rounds[-1]["best"]
    improve = first_best - final_best
    improve_pct = improve / first_best * 100 if first_best else 0

    param_rows = ""
    for k in ["t_lead", "tau", "ext_max", "hold_max", "d_up"]:
        if k in best:
            param_rows += f'<tr><td>{k}</td><td class="v">{best[k]}</td></tr>'

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>B4 BO 수렴 — {run_id}</title>
<style>
  body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:#111827; background:#f9fafb; }}
  .wrap {{ max-width:980px; margin:0 auto; padding:24px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#6b7280; font-size:13px; margin-bottom:20px; }}
  .cards {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:20px; }}
  .card {{ background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:14px 18px; flex:1; min-width:150px; }}
  .card .label {{ font-size:12px; color:#6b7280; }}
  .card .num {{ font-size:22px; font-weight:600; margin-top:4px; }}
  .panel {{ background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:16px; margin-bottom:20px; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  td {{ padding:6px 10px; border-bottom:1px solid #f0f0f0; }}
  td.v {{ text-align:right; font-variant-numeric:tabular-nums; font-weight:600; }}
  .legend {{ font-size:12px; color:#6b7280; margin-top:8px; }}
  .dot {{ display:inline-block; width:10px; height:10px; border-radius:50%; vertical-align:middle; margin:0 4px 0 12px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>B4 theta Bayesian Optimization — 수렴 과정</h1>
  <div class="sub">run_id: {run_id} · status: {summary.get('status','?')} · completed_round: {summary.get('completed_round','?')} · workers: {summary.get('workers','?')}</div>

  <div class="cards">
    <div class="card"><div class="label">최적 score (낮을수록 좋음)</div><div class="num">{final_best:.1f}s</div></div>
    <div class="card"><div class="label">초기 best → 개선폭</div><div class="num">{improve:.1f}s<span style="font-size:13px;color:#6b7280"> ({improve_pct:.1f}%)</span></div></div>
    <div class="card"><div class="label">총 평가 / 실패penalty</div><div class="num">{len(evals)} <span style="font-size:13px;color:#6b7280">/ {n_penalty}</span></div></div>
    <div class="card"><div class="label">최적 발견 라운드</div><div class="num">r{next((r['round'] for r in rounds if r['best']==final_best), '?')}</div></div>
  </div>

  <div class="panel">
    <svg viewBox="0 0 {W} {H}" width="100%">
      {grid_svg}
      <polyline points="{best_pts}" fill="none" stroke="#ef4444" stroke-width="2.5"/>
      {dots_svg}
      <text x="{ml+pw/2}" y="{H-8}" text-anchor="middle" font-size="12" fill="#374151">BO round</text>
      <text x="16" y="{mt+ph/2}" text-anchor="middle" font-size="12" fill="#374151" transform="rotate(-90 16 {mt+ph/2})">score_sec</text>
    </svg>
    <div class="legend">
      <span class="dot" style="background:#3b82f6"></span>각 평가 score
      <span class="dot" style="background:#ef4444"></span>best-so-far (수렴선)
      · 실패 penalty {n_penalty}건은 그래프에서 제외
    </div>
  </div>

  <div class="panel">
    <div style="font-weight:600; margin-bottom:8px;">최적 theta — {best.get('parameter_id','')}</div>
    <table>{param_rows}<tr><td>bo_score_sec</td><td class="v">{best.get('bo_score_sec','')}</td></tr></table>
  </div>
</div>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize compact_v9 B4 theta BO convergence")
    parser.add_argument("--run-id", default="b4_theta_bo_001")
    parser.add_argument("--output", type=Path, default=HTML_OUTPUT_DIR / "b4_bo_progress.html")
    args = parser.parse_args()

    run_dir = B4_BO_DIR / args.run_id
    if not run_dir.is_dir():
        raise SystemExit(f"run dir not found: {run_dir}")

    rounds = load_rounds(run_dir)
    evals = load_evals(run_dir)
    summary = load_summary(run_dir)

    html = build_html(args.run_id, rounds, evals, summary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(f"Wrote B4 BO progress HTML to {args.output}")
    print(f"  final best: {rounds[-1]['best']:.1f}s  (from {rounds[0]['best']:.1f}s)")


if __name__ == "__main__":
    main()
