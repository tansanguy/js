#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import MaxNLocator
from scipy import stats


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_CSV = PROJECT_ROOT / "gcpcsv" / "final result" / "clean" / "BO_06092213.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "figures" / "bo_round_mean_ci_06092213"

NAVY = "#0B1F3A"
BLUE = "#1F66B5"
BLUE_LIGHT = "#CFE0F2"
ORANGE = "#F2A541"
GRAY = "#8B98A8"
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
            "axes.edgecolor": "#C7D1DC",
            "axes.labelcolor": TEXT,
            "axes.titlecolor": NAVY,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "axes.unicode_minus": False,
            "legend.fontsize": 8.8,
        }
    )


def resolve_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_valid(input_csv: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = pd.read_csv(input_csv)
    required = {"round", "round_theta_index", "score", "penalty", "final_status"}
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    for col in ["round", "round_theta_index", "score", "penalty", "best_so_far"]:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")

    valid = raw[
        raw["round"].notna()
        & raw["score"].notna()
        & raw["score"].lt(10000)
        & raw["penalty"].fillna(0).eq(0)
        & raw["final_status"].fillna("PASS").astype(str).str.upper().eq("PASS")
    ].copy()
    if valid.empty:
        raise ValueError("No valid PASS/no-penalty score rows found.")
    return raw, valid


def build_round_summary(raw: pd.DataFrame, valid: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for round_id in range(int(raw["round"].min()), int(raw["round"].max()) + 1):
        all_group = raw[raw["round"].eq(round_id)]
        group = valid[valid["round"].eq(round_id)]
        if group.empty:
            rows.append(
                {
                    "round": round_id,
                    "valid_n": 0,
                    "total_n": int(len(all_group)),
                    "excluded_n": int(len(all_group)),
                    "mean_score": np.nan,
                    "std_score": np.nan,
                    "q1_score": np.nan,
                    "q3_score": np.nan,
                    "ci95": np.nan,
                    "ci95_low": np.nan,
                    "ci95_high": np.nan,
                    "round_best_score": np.nan,
                    "round_best_theta_index": np.nan,
                    "round_best_parameter_id": "",
                }
            )
            continue
        scores = group["score"].astype(float)
        n = int(len(scores))
        std = float(scores.std(ddof=1)) if n > 1 else 0.0
        se = std / math.sqrt(n) if n > 1 else 0.0
        ci95 = float(stats.t.ppf(0.975, n - 1) * se) if n > 1 else 0.0
        best = group.loc[group["score"].idxmin()]
        rows.append(
            {
                "round": round_id,
                "valid_n": n,
                "total_n": int(len(all_group)),
                "excluded_n": int(len(all_group) - n),
                "mean_score": float(scores.mean()),
                "std_score": std,
                "q1_score": float(scores.quantile(0.25)),
                "q3_score": float(scores.quantile(0.75)),
                "ci95": ci95,
                "ci95_low": float(scores.mean() - ci95),
                "ci95_high": float(scores.mean() + ci95),
                "round_best_score": float(best["score"]),
                "round_best_theta_index": int(best["round_theta_index"]),
                "round_best_parameter_id": str(best.get("parameter_id", "")),
            }
        )
    summary = pd.DataFrame(rows)
    summary["best_so_far_score"] = summary["round_best_score"].cummin()
    summary["mean_score_trend"] = summary["mean_score"].rolling(window=5, center=True, min_periods=1).mean()
    return summary


def smooth_xy(x: np.ndarray, y: np.ndarray, samples: int = 300) -> tuple[np.ndarray, np.ndarray]:
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    if len(x) < 4:
        grid = np.linspace(float(x.min()), float(x.max()), samples)
        return grid, np.interp(grid, x, y)
    try:
        from scipy.interpolate import PchipInterpolator  # type: ignore

        grid = np.linspace(float(x.min()), float(x.max()), samples)
        return grid, PchipInterpolator(x, y)(grid)
    except Exception:
        grid = np.linspace(float(x.min()), float(x.max()), samples)
        return grid, np.interp(grid, x, y)


def axis_clean(ax: plt.Axes) -> None:
    ax.set_facecolor(BACKGROUND)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    ax.grid(True, color=GRID, linewidth=0.85, alpha=0.9)


def plot(summary: pd.DataFrame, output_dir: Path) -> None:
    valid_summary = summary[summary["valid_n"].gt(0)].copy()
    x = valid_summary["round"].to_numpy(dtype=float)
    mean = valid_summary["mean_score_trend"].to_numpy(dtype=float)
    q1 = valid_summary["q1_score"].to_numpy(dtype=float)
    q3 = valid_summary["q3_score"].to_numpy(dtype=float)
    iqr = q3 - q1
    box_low = mean - iqr / 2.0
    box_high = mean + iqr / 2.0
    grid, mean_smooth = smooth_xy(x, mean)

    best_row = valid_summary.loc[valid_summary["round_best_score"].idxmin()]

    fig, ax = plt.subplots(figsize=(11.4, 6.3), constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    fig.subplots_adjust(left=0.105, right=0.97, top=0.82, bottom=0.13)
    axis_clean(ax)

    box_width = 0.48
    for round_x, low_y, high_y, mean_y in zip(x, box_low, box_high, mean, strict=False):
        if not np.isfinite(low_y) or not np.isfinite(high_y) or not np.isfinite(mean_y):
            continue
        height = max(float(high_y - low_y), 0.8)
        ax.add_patch(
            Rectangle(
                (float(round_x) - box_width / 2.0, float(low_y)),
                box_width,
                height,
                facecolor=BLUE_LIGHT,
                edgecolor="#AFC9E8",
                linewidth=0.85,
                alpha=0.78,
                zorder=1,
            )
        )
        ax.hlines(
            float(mean_y),
            float(round_x) - box_width / 2.0,
            float(round_x) + box_width / 2.0,
            color=BLUE,
            linewidth=1.15,
            alpha=0.85,
            zorder=2,
        )
    ax.plot(grid, mean_smooth, color=BLUE, lw=2.8, label="Round mean trend", zorder=3)
    ax.scatter(
        valid_summary["round"],
        valid_summary["round_best_score"],
        s=38,
        color=NAVY,
        alpha=0.78,
        edgecolor="#172436",
        linewidth=0.7,
        label="Round-best score",
        zorder=4,
    )
    ax.scatter(
        [best_row["round"]],
        [best_row["round_best_score"]],
        s=92,
        color=ORANGE,
        edgecolor=NAVY,
        linewidth=1.2,
        label="Best observed",
        zorder=5,
    )
    ax.annotate(
        f"best observed\nRound {int(best_row['round'])}",
        xy=(best_row["round"], best_row["round_best_score"]),
        xytext=(min(float(best_row["round"]) + 8, 46), float(best_row["round_best_score"]) + 32),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.1),
        fontsize=9.2,
        color=NAVY,
        ha="left",
    )

    ax.set_title("BO Round Mean Score Boxplot View", loc="left", fontsize=16.0, fontweight="bold", pad=14)
    ax.set_xlabel("Round", fontsize=10.8)
    ax.set_ylabel("Score", fontsize=10.8)
    ax.set_xlim(1, 50)
    y_min = min(float(valid_summary["round_best_score"].quantile(0.02)), float(np.nanquantile(box_low, 0.04)))
    y_max = max(float(np.nanquantile(mean, 0.96)), float(np.nanquantile(box_high, 0.96)))
    y_pad = max((y_max - y_min) * 0.08, 8.0)
    ax.set_ylim(y_min - y_pad, y_max + y_pad)
    ax.set_xticks([1, 10, 20, 30, 40, 50])
    ax.yaxis.set_major_locator(MaxNLocator(7))

    handles = [
        Patch(facecolor=BLUE_LIGHT, edgecolor="#AFC9E8", label="Q1-Q3 box"),
        Line2D([0], [0], color=BLUE, lw=2.8, label="Round mean trend"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=NAVY, markeredgecolor="#172436", markersize=6, label="Round-best score"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=ORANGE, markeredgecolor=NAVY, markersize=8, label="Best observed"),
    ]
    ax.legend(handles=handles, loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)

    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / "bo_round_mean_ci_with_best_points.png", bbox_inches="tight")
    fig.savefig(output_dir / "bo_round_mean_ci_with_best_points.svg", bbox_inches="tight")
    plt.close(fig)


def build(args: argparse.Namespace) -> None:
    configure_style()
    input_csv = resolve_path(args.input_csv)
    output_dir = resolve_path(args.output_dir)
    raw, valid = load_valid(input_csv)
    summary = build_round_summary(raw, valid)
    output_dir.mkdir(parents=True, exist_ok=True)
    valid.to_csv(output_dir / "bo_valid_evaluations_used.csv", index=False)
    summary.to_csv(output_dir / "bo_round_mean_ci_summary.csv", index=False)
    plot(summary, output_dir)
    print(output_dir)
    print("raw_rows", len(raw), "valid_rows", len(valid), "excluded_rows", len(raw) - len(valid))
    print(summary[["round", "valid_n", "mean_score", "q1_score", "q3_score", "round_best_score"]].head(12).round(3).to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build BO round mean score with Q1-Q3 boxplot-style figure.")
    parser.add_argument("--input-csv", default=str(DEFAULT_INPUT_CSV))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser.parse_args()


def main() -> None:
    build(parse_args())


if __name__ == "__main__":
    main()
