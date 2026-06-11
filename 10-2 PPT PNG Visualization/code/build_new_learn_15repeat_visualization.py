#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = (
    PROJECT_ROOT
    / "gcpcsv"
    / "final result"
    / "clean"
    / "New_Learn"
    / "gcp_metrics"
    / "new_learn_bo16_seoul15_gcp_20260609_225217"
    / "robust_selection"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "figures" / "new_learn_15repeat"

THETA_FIELDS = ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]
THETA_LABELS = ["Lead Time", "ETA Gate", "Green Ext.", "Queue Ratio", "Spillback"]

NAVY = "#0B1F3A"
BLUE = "#2377BD"
BLUE_DARK = "#155A96"
ORANGE = "#F2A541"
GREEN = "#2A9D78"
GRAY = "#95A3B3"
LIGHT_BLUE = "#DCEAF6"
GRID = "#DDE5EE"
TEXT = "#25364A"
MUTED = "#6B7788"
BACKGROUND = "#FBFCFD"


def configure_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 160,
            "savefig.dpi": 260,
            "font.family": "DejaVu Sans",
            "axes.edgecolor": "#CAD3DF",
            "axes.labelcolor": TEXT,
            "axes.titlecolor": NAVY,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "axes.unicode_minus": False,
        }
    )


def resolve_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def short_id(parameter_id: str) -> str:
    parts = str(parameter_id).split("_")
    if len(parts) >= 3 and parts[0] == "bo":
        return f"{parts[1]}_{parts[2]}"
    return str(parameter_id)[:12]


def load_inputs(run_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    summary_path = run_dir / "survivor_ranking.csv"
    if not summary_path.exists():
        summary_path = run_dir / "mini_batch_theta_summary.csv"
    candidates_path = run_dir / "robust_theta_candidates.csv"
    selection_path = run_dir / "selected_for_final_theta_candidates.csv"
    manifest_path = run_dir / "robust_selection_summary.json"

    summary = pd.read_csv(summary_path)
    candidates = pd.read_csv(candidates_path) if candidates_path.exists() else pd.DataFrame()
    selected = pd.read_csv(selection_path) if selection_path.exists() else pd.DataFrame()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    for frame in [summary, candidates, selected]:
        for col in [
            "theta_rank",
            "mean_score",
            "mean_D_E_sec",
            "mean_D_G_sec",
            "mean_B4_vs_B04_D_E_improvement_sec",
            "repeat_count",
            "arrival_rate",
            "stuck_count",
            "fail_count",
            "teleport_count",
            *THETA_FIELDS,
        ]:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")

    if "mean_score" not in summary.columns:
        raise ValueError(f"No mean_score column in {summary_path}")

    selected_ids = set(selected.get("parameter_id", pd.Series(dtype=str)).astype(str))
    candidate_source = candidates[["parameter_id", "source_score", "selection_reason"]].copy() if not candidates.empty else pd.DataFrame()
    if not candidate_source.empty:
        summary = summary.merge(candidate_source, on="parameter_id", how="left")
    summary = summary.sort_values("mean_score", ascending=True, kind="mergesort").reset_index(drop=True)
    summary["robust_rank"] = np.arange(1, len(summary) + 1)
    summary["short_id"] = summary["parameter_id"].map(short_id)
    summary["label"] = summary.apply(lambda r: f"R{int(r['robust_rank']):02d}  T{int(r['theta_rank']):02d}  {r['short_id']}", axis=1)
    summary["selected_for_final"] = summary["parameter_id"].astype(str).isin(selected_ids)
    summary["all_repeats_ok"] = (
        summary.get("repeat_count", 0).fillna(0).eq(15)
        & summary.get("arrival_rate", 0).fillna(0).ge(1.0)
        & summary.get("stuck_count", 0).fillna(0).eq(0)
        & summary.get("fail_count", 0).fillna(0).eq(0)
        & summary.get("teleport_count", 0).fillna(0).eq(0)
    )
    return summary, selected, manifest


def axis_clean(ax: plt.Axes, grid_axis: str = "x") -> None:
    ax.set_facecolor(BACKGROUND)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    if grid_axis == "x":
        ax.grid(True, axis="x", color=GRID, linewidth=0.8, alpha=0.9)
        ax.grid(False, axis="y")
    elif grid_axis == "y":
        ax.grid(True, axis="y", color=GRID, linewidth=0.8, alpha=0.9)
        ax.grid(False, axis="x")


def normalize_columns(df: pd.DataFrame, fields: list[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for field in fields:
        values = df[field].astype(float)
        span = values.max() - values.min()
        out[field] = 0.5 if span <= 1.0e-12 else (values - values.min()) / span
    return out


def plot_dashboard(summary: pd.DataFrame, manifest: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    top = summary.iloc[0]
    repeat_count = int(summary["repeat_count"].median()) if "repeat_count" in summary.columns else 15
    candidate_count = len(summary)
    all_ok_count = int(summary["all_repeats_ok"].sum())

    fig = plt.figure(figsize=(15.6, 9.2), constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    gs = fig.add_gridspec(
        3,
        3,
        height_ratios=[0.20, 0.52, 0.28],
        width_ratios=[1.45, 1.0, 1.0],
        left=0.055,
        right=0.985,
        top=0.935,
        bottom=0.075,
        wspace=0.34,
        hspace=0.62,
    )

    ax_title = fig.add_subplot(gs[0, :])
    ax_title.axis("off")
    ax_title.text(
        0.0,
        0.84,
        "Latest 15-Repeat Robust Selection",
        color=NAVY,
        fontsize=21,
        fontweight="bold",
        ha="left",
        va="top",
    )
    run_id = manifest.get("run_id", "new_learn_bo16_seoul15_gcp_20260609_225217")
    subtitle = (
        f"{candidate_count} BO candidates x {repeat_count} repeats | "
        f"{all_ok_count}/{candidate_count} full-arrival, no stuck/fail/teleport | "
        f"Route: {manifest.get('route_id', 'FINAL_DEST_ER_ACC_006')}"
    )
    ax_title.text(0.0, 0.38, subtitle, color=MUTED, fontsize=10.5, ha="left", va="top")
    ax_title.text(0.0, 0.08, str(run_id), color="#8996A7", fontsize=8.8, ha="left", va="top")

    kpi = [
        ("Best Mean Score", f"{top['mean_score']:.2f}", "lower is better"),
        ("Emergency Delay", f"{top['mean_D_E_sec']:.1f}s", "best candidate"),
        ("General Delay", f"{top['mean_D_G_sec']:.1f}s", "best candidate"),
        ("EMV Improvement", f"{top['mean_B4_vs_B04_D_E_improvement_sec']:.1f}s", "vs baseline"),
    ]
    for i, (label, value, note) in enumerate(kpi):
        x = 0.50 + i * 0.125
        ax_title.text(x, 0.74, label, color=MUTED, fontsize=8.8, ha="left")
        ax_title.text(x, 0.43, value, color=NAVY, fontsize=17, fontweight="bold", ha="left")
        ax_title.text(x, 0.14, note, color="#8996A7", fontsize=8.3, ha="left")

    ax_rank = fig.add_subplot(gs[1:, 0])
    axis_clean(ax_rank, "x")
    y = np.arange(len(summary))
    colors = np.where(summary["selected_for_final"], BLUE, "#B9C4D0")
    edge_colors = np.where(summary["robust_rank"].eq(1), ORANGE, colors)
    ax_rank.barh(y, summary["mean_score"], color=colors, alpha=0.30, edgecolor="none", height=0.68)
    ax_rank.scatter(summary["mean_score"], y, s=72, color=colors, edgecolor=edge_colors, linewidth=1.5, zorder=3)
    ax_rank.scatter([top["mean_score"]], [0], s=130, color=ORANGE, edgecolor=NAVY, linewidth=1.2, zorder=4)
    for _, row in summary.iterrows():
        if row["robust_rank"] <= 5:
            ax_rank.text(row["mean_score"] + 0.35, row["robust_rank"] - 1, f"{row['mean_score']:.1f}", fontsize=8.7, va="center", color=NAVY)
    ax_rank.set_yticks(y)
    ax_rank.set_yticklabels(summary["label"], fontsize=8.6)
    ax_rank.invert_yaxis()
    ax_rank.set_xlabel("Mean Objective Score", fontsize=10.2)
    ax_rank.set_title("Ranked Mean Score", loc="left", fontsize=13.5, fontweight="bold", pad=10)
    xmin = max(0, float(summary["mean_score"].min()) - 8)
    xmax = float(summary["mean_score"].max()) + 8
    ax_rank.set_xlim(xmin, xmax)
    ax_rank.xaxis.set_major_locator(MaxNLocator(6))

    ax_trade = fig.add_subplot(gs[1, 1])
    axis_clean(ax_trade, "y")
    score_norm = Normalize(vmin=float(summary["mean_score"].min()), vmax=float(summary["mean_score"].max()))
    cmap = LinearSegmentedColormap.from_list("score", [GREEN, BLUE, ORANGE])
    scatter = ax_trade.scatter(
        summary["mean_D_E_sec"],
        summary["mean_D_G_sec"],
        c=summary["mean_score"],
        cmap=cmap,
        norm=score_norm,
        s=np.where(summary["selected_for_final"], 95, 54),
        edgecolor=np.where(summary["selected_for_final"], NAVY, "white"),
        linewidth=np.where(summary["selected_for_final"], 1.05, 0.65),
        alpha=0.95,
    )
    ax_trade.scatter([top["mean_D_E_sec"]], [top["mean_D_G_sec"]], s=150, color=ORANGE, edgecolor=NAVY, linewidth=1.2, zorder=5)
    for _, row in summary.head(5).iterrows():
        ax_trade.text(row["mean_D_E_sec"] + 0.35, row["mean_D_G_sec"] + 0.2, f"R{int(row['robust_rank']):02d}", fontsize=8.2, color=NAVY)
    ax_trade.set_xlabel("Emergency Delay (s)", fontsize=10.0)
    ax_trade.set_ylabel("General Traffic Delay (s)", fontsize=10.0)
    ax_trade.set_title("Emergency-General Trade-off", loc="left", fontsize=13.5, fontweight="bold", pad=10)
    cb = fig.colorbar(scatter, ax=ax_trade, fraction=0.045, pad=0.03)
    cb.set_label("Mean Score", fontsize=8.7)
    cb.ax.tick_params(labelsize=8)

    ax_compare = fig.add_subplot(gs[1, 2])
    axis_clean(ax_compare, "y")
    if "source_score" in summary.columns and summary["source_score"].notna().any():
        ax_compare.scatter(summary["source_score"], summary["mean_score"], s=58, color="#8FA6BC", edgecolor="white", linewidth=0.6, alpha=0.9)
        ax_compare.scatter(summary.head(5)["source_score"], summary.head(5)["mean_score"], s=84, color=BLUE, edgecolor=NAVY, linewidth=0.8)
        ax_compare.scatter([top["source_score"]], [top["mean_score"]], s=140, color=ORANGE, edgecolor=NAVY, linewidth=1.2, zorder=5)
        for _, row in summary.head(5).iterrows():
            ax_compare.text(row["source_score"] + 0.8, row["mean_score"] + 0.35, f"R{int(row['robust_rank']):02d}", fontsize=8.2, color=NAVY)
        lim_min = min(float(summary["source_score"].min()), float(summary["mean_score"].min())) - 8
        lim_max = max(float(summary["source_score"].max()), float(summary["mean_score"].max())) + 8
        ax_compare.plot([lim_min, lim_max], [lim_min, lim_max], color="#C6D0DB", lw=1.0, linestyle="--")
        ax_compare.set_xlim(lim_min, lim_max)
        ax_compare.set_ylim(float(summary["mean_score"].min()) - 5, float(summary["mean_score"].max()) + 5)
        ax_compare.set_xlabel("Original BO Score", fontsize=10.0)
        ax_compare.set_ylabel("15-Repeat Mean Score", fontsize=10.0)
        ax_compare.set_title("Original vs Repeated Score", loc="left", fontsize=13.5, fontweight="bold", pad=10)
    else:
        ax_compare.axis("off")

    ax_heat = fig.add_subplot(gs[2, 1:])
    heat = normalize_columns(summary, THETA_FIELDS)
    image = ax_heat.imshow(heat.T, aspect="auto", interpolation="nearest", cmap="Blues", vmin=0, vmax=1)
    ax_heat.set_yticks(np.arange(len(THETA_FIELDS)))
    ax_heat.set_yticklabels(THETA_LABELS, fontsize=8.9)
    ax_heat.set_xticks(np.arange(len(summary)))
    ax_heat.set_xticklabels([f"R{int(r)}" for r in summary["robust_rank"]], fontsize=8.3)
    ax_heat.tick_params(length=0)
    ax_heat.set_title("Parameter Pattern by Robust Rank", loc="left", fontsize=13.5, fontweight="bold", pad=10)
    for spine in ax_heat.spines.values():
        spine.set_visible(False)
    for x_idx, selected in enumerate(summary["selected_for_final"]):
        if selected:
            ax_heat.add_patch(plt.Rectangle((x_idx - 0.5, -0.5), 1, len(THETA_FIELDS), fill=False, edgecolor=ORANGE, linewidth=1.3))
    for y_idx, field in enumerate(THETA_FIELDS):
        for x_idx, value in enumerate(summary[field]):
            if x_idx < 5:
                ax_heat.text(x_idx, y_idx, f"{value:g}", ha="center", va="center", fontsize=6.8, color=NAVY)
    cb2 = fig.colorbar(image, ax=ax_heat, fraction=0.025, pad=0.012)
    cb2.set_label("Normalized value", fontsize=8.2)
    cb2.ax.tick_params(labelsize=7.8)

    legend_items = [
        Patch(facecolor=BLUE, alpha=0.30, label="Selected for final validation"),
        Patch(facecolor="#B9C4D0", alpha=0.30, label="Other survivor"),
        Patch(facecolor=ORANGE, label="Best mean score"),
    ]
    fig.legend(handles=legend_items, loc="lower left", bbox_to_anchor=(0.055, 0.012), ncol=3, frameon=False, fontsize=8.7)

    fig.savefig(output_dir / "latest_15repeat_robust_selection_dashboard.png", bbox_inches="tight")
    fig.savefig(output_dir / "latest_15repeat_robust_selection_dashboard.svg", bbox_inches="tight")
    plt.close(fig)


def plot_score_ranking(summary: pd.DataFrame, manifest: dict[str, Any], output_dir: Path) -> None:
    top = summary.iloc[0]
    repeat_count = int(summary["repeat_count"].median()) if "repeat_count" in summary.columns else 15
    candidate_count = len(summary)
    all_ok_count = int(summary["all_repeats_ok"].sum())

    fig, ax = plt.subplots(figsize=(12.4, 7.1), constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    fig.subplots_adjust(left=0.26, right=0.97, top=0.78, bottom=0.12)
    axis_clean(ax, "x")

    y = np.arange(len(summary))
    bar_colors = np.where(summary["selected_for_final"], BLUE, "#D8DEE6")
    dot_colors = np.where(summary["selected_for_final"], BLUE, "#9EACBA")
    ax.barh(y, summary["mean_score"], height=0.66, color=bar_colors, alpha=0.34, edgecolor="none")
    ax.scatter(summary["mean_score"], y, s=58, color=dot_colors, edgecolor="white", linewidth=0.8, zorder=3)
    ax.scatter([top["mean_score"]], [0], s=122, color=ORANGE, edgecolor=NAVY, linewidth=1.2, zorder=5)

    for _, row in summary.iterrows():
        text_color = NAVY if row["robust_rank"] <= 5 else MUTED
        if row["robust_rank"] <= 8:
            ax.text(row["mean_score"] + 0.35, row["robust_rank"] - 1, f"{row['mean_score']:.1f}", fontsize=8.7, va="center", color=text_color)

    labels = [
        f"R{int(r.robust_rank):02d}  T{int(r.theta_rank):02d}  {r.short_id}"
        for r in summary.itertuples()
    ]
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8.8)
    ax.invert_yaxis()
    ax.set_xlabel("15-Repeat Mean Objective Score", fontsize=10.8)
    ax.set_xlim(float(summary["mean_score"].min()) - 3.5, float(summary["mean_score"].max()) + 5.5)
    ax.xaxis.set_major_locator(MaxNLocator(6))

    fig.text(0.26, 0.935, "Robust Candidate Ranking", color=NAVY, fontsize=17.0, fontweight="bold", ha="left")
    fig.text(
        0.26,
        0.895,
        f"{candidate_count} BO candidates x {repeat_count} repeats | {all_ok_count}/{candidate_count} full-arrival, no stuck/fail/teleport | lower is better",
        color=MUTED,
        fontsize=9.4,
        ha="left",
    )
    handles = [
        Patch(facecolor=BLUE, alpha=0.34, label="Selected for final validation"),
        Patch(facecolor="#D8DEE6", alpha=0.70, label="Other survivor"),
        Patch(facecolor=ORANGE, label="Best mean score"),
    ]
    ax.legend(handles=handles, loc="lower right", frameon=True, facecolor="white", edgecolor="#E1E7EF", fontsize=8.8)

    fig.savefig(output_dir / "latest_15repeat_score_ranking.png", bbox_inches="tight")
    fig.savefig(output_dir / "latest_15repeat_score_ranking.svg", bbox_inches="tight")
    plt.close(fig)


def plot_tradeoff_and_parameters(summary: pd.DataFrame, output_dir: Path) -> None:
    fig = plt.figure(figsize=(13.2, 6.2), constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.22], left=0.075, right=0.985, top=0.84, bottom=0.14, wspace=0.32)

    fig.text(0.075, 0.92, "Trade-off and Parameter Pattern", color=NAVY, fontsize=17.0, fontweight="bold", ha="left")
    fig.text(0.075, 0.875, "Ranks are based on 15-repeat mean objective score; orange outline marks final-validation selections.", color=MUTED, fontsize=9.5, ha="left")

    ax_trade = fig.add_subplot(gs[0, 0])
    axis_clean(ax_trade, "y")
    score_norm = Normalize(vmin=float(summary["mean_score"].min()), vmax=float(summary["mean_score"].max()))
    cmap = LinearSegmentedColormap.from_list("score", [GREEN, BLUE, ORANGE])
    selected = summary["selected_for_final"].to_numpy()
    scatter = ax_trade.scatter(
        summary["mean_D_E_sec"],
        summary["mean_D_G_sec"],
        c=summary["mean_score"],
        cmap=cmap,
        norm=score_norm,
        s=np.where(selected, 124, 72),
        edgecolor=np.where(selected, NAVY, "white"),
        linewidth=np.where(selected, 1.15, 0.75),
        alpha=0.95,
    )
    top = summary.iloc[0]
    ax_trade.scatter([top["mean_D_E_sec"]], [top["mean_D_G_sec"]], s=170, color=ORANGE, edgecolor=NAVY, linewidth=1.35, zorder=5)
    for _, row in summary.head(8).iterrows():
        ax_trade.text(row["mean_D_E_sec"] + 0.35, row["mean_D_G_sec"] + 0.16, f"R{int(row['robust_rank']):02d}", fontsize=8.2, color=NAVY)
    ax_trade.set_xlabel("Emergency Delay (s)", fontsize=10.2)
    ax_trade.set_ylabel("General Traffic Delay (s)", fontsize=10.2)
    ax_trade.set_title("Emergency-General Delay Trade-off", loc="left", fontsize=12.4, fontweight="bold", pad=9)
    cb = fig.colorbar(scatter, ax=ax_trade, fraction=0.048, pad=0.03)
    cb.set_label("Mean Score", fontsize=8.8)
    cb.ax.tick_params(labelsize=8)

    ax_heat = fig.add_subplot(gs[0, 1])
    heat = normalize_columns(summary, THETA_FIELDS)
    image = ax_heat.imshow(heat.T, aspect="auto", interpolation="nearest", cmap="Blues", vmin=0, vmax=1)
    ax_heat.set_yticks(np.arange(len(THETA_FIELDS)))
    ax_heat.set_yticklabels(THETA_LABELS, fontsize=9.2)
    ax_heat.set_xticks(np.arange(len(summary)))
    ax_heat.set_xticklabels([f"R{int(r)}" for r in summary["robust_rank"]], fontsize=8.2)
    ax_heat.tick_params(length=0)
    ax_heat.set_title("Normalized Parameter Values by Rank", loc="left", fontsize=12.4, fontweight="bold", pad=9)
    for spine in ax_heat.spines.values():
        spine.set_visible(False)
    for x_idx, selected_value in enumerate(summary["selected_for_final"]):
        if selected_value:
            ax_heat.add_patch(plt.Rectangle((x_idx - 0.5, -0.5), 1, len(THETA_FIELDS), fill=False, edgecolor=ORANGE, linewidth=1.25))
    for y_idx, field in enumerate(THETA_FIELDS):
        for x_idx, value in enumerate(summary[field]):
            if x_idx < 5:
                ax_heat.text(x_idx, y_idx, f"{value:g}", ha="center", va="center", fontsize=7.0, color=NAVY)
    cb2 = fig.colorbar(image, ax=ax_heat, fraction=0.035, pad=0.018)
    cb2.set_label("Normalized parameter value", fontsize=8.4)
    cb2.ax.tick_params(labelsize=8)

    fig.savefig(output_dir / "latest_15repeat_tradeoff_parameters.png", bbox_inches="tight")
    fig.savefig(output_dir / "latest_15repeat_tradeoff_parameters.svg", bbox_inches="tight")
    plt.close(fig)


def write_tables(summary: pd.DataFrame, output_dir: Path) -> None:
    columns = [
        "robust_rank",
        "theta_rank",
        "parameter_id",
        *THETA_FIELDS,
        "repeat_count",
        "arrival_rate",
        "stuck_count",
        "fail_count",
        "teleport_count",
        "mean_D_E_sec",
        "mean_D_G_sec",
        "mean_score",
        "mean_B4_vs_B04_D_E_improvement_sec",
        "stage2_hold_mean",
        "stage3_preemption_mean",
        "selected_for_final",
        "source_score",
        "selection_reason",
    ]
    available = [col for col in columns if col in summary.columns]
    summary[available].to_csv(output_dir / "latest_15repeat_clean_summary.csv", index=False)


def build(args: argparse.Namespace) -> None:
    configure_style()
    run_dir = resolve_path(args.run_dir)
    output_dir = resolve_path(args.output_dir)
    summary, _selected, manifest = load_inputs(run_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_tables(summary, output_dir)
    plot_dashboard(summary, manifest, output_dir)
    plot_score_ranking(summary, manifest, output_dir)
    plot_tradeoff_and_parameters(summary, output_dir)
    print(output_dir / "latest_15repeat_robust_selection_dashboard.png")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build enhanced latest 15-repeat BO robust selection visualization.")
    parser.add_argument("--run-dir", default=str(DEFAULT_RUN_DIR), help="Robust selection output directory.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Figure output directory.")
    return parser.parse_args()


def main() -> None:
    build(parse_args())


if __name__ == "__main__":
    main()
