#!/usr/bin/env python3
"""Build B0 baseline vs Top 3 B4 repeated-simulation boxplot."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

try:
    from scipy import stats
except Exception:  # pragma: no cover - fallback for minimal environments
    stats = None


ROOT = Path("/Users/junlee/Desktop/js")
B4_PATH = ROOT / "gcpcsv/final result/clean/New_Learn 2/Top3_Final_Results/Final_result_all.csv"
B0_PATH = (
    ROOT
    / "gcpcsv/final result/clean/New_Learn 2/Top3_Final_Results/GCP_B0_only/B0_no_control_best_300_clean.csv"
)
OUT_DIR = ROOT / "results/figures/dongho_b0_b4_top3_boxplot"

OUTPUT_PNG = OUT_DIR / "dongho_b0_b4_top3_objective_boxplot.png"
OUTPUT_SVG = OUT_DIR / "dongho_b0_b4_top3_objective_boxplot.svg"
OUTPUT_SUMMARY = OUT_DIR / "dongho_b0_b4_top3_boxplot_summary.csv"
OUTPUT_CLEAN = OUT_DIR / "dongho_b0_b4_top3_boxplot_source_clean.csv"

B4_ORDER = [
    "bo_r44_003_tl31_dt129_ge6_qr62_tau74",
    "bo_r32_003_tl88_dt140_ge24_qr24_tau82",
    "bo_r36_005_tl20_dt125_ge9_qr54_tau82",
]

GROUPS = [
    {
        "group_key": "B0_no_control",
        "parameter_id": "B0_no_control",
        "display_label": "B0 no control",
        "source_mode": "B0_no_control",
        "kind": "baseline",
    },
    {
        "group_key": B4_ORDER[0],
        "parameter_id": B4_ORDER[0],
        "display_label": "r44\n003\ntl31_dt129_ge6_qr62_tau74",
        "source_mode": "B4",
        "kind": "best_b4",
    },
    {
        "group_key": B4_ORDER[1],
        "parameter_id": B4_ORDER[1],
        "display_label": "r32\n003\ntl88_dt140_ge24_qr24_tau82",
        "source_mode": "B4",
        "kind": "b4",
    },
    {
        "group_key": B4_ORDER[2],
        "parameter_id": B4_ORDER[2],
        "display_label": "r36\n005\ntl20_dt125_ge9_qr54_tau82",
        "source_mode": "B4",
        "kind": "b4",
    },
]


def _coerce_failed(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    normalized = series.astype(str).str.strip().str.lower()
    return normalized.isin({"true", "1", "yes", "y", "failed"})


def _require_columns(df: pd.DataFrame, cols: list[str], path: Path) -> None:
    missing = [col for col in cols if col not in df.columns]
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")


def _filter_valid(df: pd.DataFrame, path: Path) -> pd.DataFrame:
    _require_columns(df, ["objective_score", "final_status", "failed"], path)
    out = df.copy()
    out["objective_score"] = pd.to_numeric(out["objective_score"], errors="coerce")
    status_ok = out["final_status"].astype(str).str.strip().str.upper().eq("PASS")
    failed = _coerce_failed(out["failed"])
    finite_score = np.isfinite(out["objective_score"].to_numpy(dtype=float))
    return out.loc[status_ok & ~failed & finite_score].copy()


def _read_clean_source() -> pd.DataFrame:
    b0 = _filter_valid(pd.read_csv(B0_PATH), B0_PATH)
    b4 = _filter_valid(pd.read_csv(B4_PATH), B4_PATH)
    _require_columns(b4, ["parameter_id"], B4_PATH)

    b4 = b4[b4["parameter_id"].isin(B4_ORDER)].copy()

    b0["group_key"] = "B0_no_control"
    b0["parameter_id"] = "B0_no_control"
    b0["display_label"] = "B0 no control"
    b0["source_mode"] = "B0_no_control"

    label_map = {g["parameter_id"]: g["display_label"] for g in GROUPS if g["source_mode"] == "B4"}
    b4["group_key"] = b4["parameter_id"]
    b4["display_label"] = b4["parameter_id"].map(label_map)
    b4["source_mode"] = "B4"

    optional_cols = [
        "repeat_id",
        "phase",
        "mode",
        "D_E_sec",
        "D_G_sec",
        "T_actual_EMV_sec",
        "T_actual_GENERAL_sec",
        "final_status",
        "failed",
    ]
    cols = [
        "group_key",
        "display_label",
        "source_mode",
        "parameter_id",
        "objective_score",
    ] + [col for col in optional_cols if col in b0.columns or col in b4.columns]

    clean = pd.concat([b0, b4], ignore_index=True, sort=False)
    clean = clean[cols].copy()
    clean["group_order"] = clean["group_key"].map({g["group_key"]: i for i, g in enumerate(GROUPS)})
    clean = clean.sort_values(["group_order", "objective_score"]).drop(columns=["group_order"])

    counts = clean.groupby("group_key").size().to_dict()
    expected = {"B0_no_control": 300, **{pid: 300 for pid in B4_ORDER}}
    if counts != expected:
        raise ValueError(f"Unexpected valid row counts. expected={expected}, actual={counts}")
    if len(clean) != 1200:
        raise ValueError(f"Expected 1200 valid rows after filtering, found {len(clean)}")
    return clean


def _ci95(values: pd.Series) -> tuple[float, float, float]:
    arr = values.to_numpy(dtype=float)
    n = len(arr)
    mean = float(np.mean(arr))
    if n < 2:
        return mean, mean, 0.0
    sem = float(np.std(arr, ddof=1) / np.sqrt(n))
    crit = float(stats.t.ppf(0.975, n - 1)) if stats is not None else 1.9679
    half = crit * sem
    return mean - half, mean + half, half


def _build_summary(clean: pd.DataFrame) -> pd.DataFrame:
    rows = []
    best_b4_mean = None
    b0_mean = None
    for group in GROUPS:
        subset = clean[clean["group_key"] == group["group_key"]]
        scores = subset["objective_score"]
        ci_low, ci_high, ci_half = _ci95(scores)
        mean = float(scores.mean())
        if group["group_key"] == "B0_no_control":
            b0_mean = mean
        if group["group_key"] == B4_ORDER[0]:
            best_b4_mean = mean
        rows.append(
            {
                "group_key": group["group_key"],
                "display_label": group["display_label"].replace("\n", " / "),
                "source_mode": group["source_mode"],
                "parameter_id": group["parameter_id"],
                "n": int(scores.size),
                "mean_score": mean,
                "std_score": float(scores.std(ddof=1)),
                "median_score": float(scores.median()),
                "q1_score": float(scores.quantile(0.25)),
                "q3_score": float(scores.quantile(0.75)),
                "min_score": float(scores.min()),
                "max_score": float(scores.max()),
                "ci95_low": ci_low,
                "ci95_high": ci_high,
                "ci95_half_width": ci_half,
                "is_best_b4": group["group_key"] == B4_ORDER[0],
            }
        )

    summary = pd.DataFrame(rows)
    summary["mean_delta_vs_b0"] = summary["mean_score"] - float(b0_mean)
    summary["mean_delta_vs_best_b4"] = summary["mean_score"] - float(best_b4_mean)
    return summary


def _draw_plot(clean: pd.DataFrame, summary: pd.DataFrame) -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10.5,
            "axes.titlesize": 16,
            "axes.titleweight": "bold",
            "axes.labelsize": 11.5,
            "axes.edgecolor": "#A7B1BE",
            "axes.linewidth": 0.9,
            "xtick.color": "#2B3A4A",
            "ytick.color": "#2B3A4A",
            "text.color": "#17263A",
            "figure.facecolor": "#FFFFFF",
            "axes.facecolor": "#FBFCFD",
            "savefig.facecolor": "#FFFFFF",
        }
    )

    group_data = [
        clean.loc[clean["group_key"] == group["group_key"], "objective_score"].to_numpy(dtype=float)
        for group in GROUPS
    ]
    labels = [group["display_label"] for group in GROUPS]
    positions = np.arange(1, len(GROUPS) + 1)

    fig, ax = plt.subplots(figsize=(12.6, 6.7), constrained_layout=False)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.80, bottom=0.2)

    box = ax.boxplot(
        group_data,
        positions=positions,
        widths=0.56,
        patch_artist=True,
        showmeans=False,
        showfliers=True,
        medianprops={"color": "#17263A", "linewidth": 1.6},
        whiskerprops={"color": "#17263A", "linewidth": 1.1},
        capprops={"color": "#17263A", "linewidth": 1.1},
        flierprops={
            "marker": "o",
            "markerfacecolor": "#FFFFFF",
            "markeredgecolor": "#17263A",
            "markersize": 4.2,
            "alpha": 0.92,
        },
    )

    colors = ["#D6DEE8", "#F36F6F", "#A8D2E8", "#A8D2E8"]
    edges = ["#687789", "#9E3632", "#477E9D", "#477E9D"]
    for patch, face, edge in zip(box["boxes"], colors, edges):
        patch.set_facecolor(face)
        patch.set_edgecolor(edge)
        patch.set_alpha(0.92)
        patch.set_linewidth(1.35)

    means = summary["mean_score"].to_numpy(dtype=float)
    ci_low = summary["ci95_low"].to_numpy(dtype=float)
    ci_high = summary["ci95_high"].to_numpy(dtype=float)
    yerr = np.vstack([means - ci_low, ci_high - means])
    ax.errorbar(
        positions,
        means,
        yerr=yerr,
        fmt="o",
        color="#17263A",
        ecolor="#17263A",
        elinewidth=1.8,
        capsize=4.5,
        capthick=1.8,
        markersize=5.2,
        zorder=5,
        label="Mean +/- 95% CI",
    )

    fig.suptitle(
        "Dongho Final Destination - B0 Baseline vs Top 3 B4 Objective Score",
        x=0.5,
        y=0.955,
        fontsize=16,
        fontweight="bold",
        color="#17263A",
    )
    ax.text(
        0.5,
        0.905,
        "B0 no-control baseline and each B4 candidate are evaluated over 300 repeated simulations.",
        transform=fig.transFigure,
        ha="center",
        va="center",
        color="#5F6D7C",
        fontsize=10.3,
    )
    ax.set_ylabel("Objective Score (lower is better)")
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.set_xlim(0.35, len(GROUPS) + 0.65)
    ax.grid(axis="y", color="#D9E1EA", linewidth=0.9, alpha=0.85)
    ax.grid(axis="x", visible=False)

    all_scores = clean["objective_score"].to_numpy(dtype=float)
    y_min = min(float(np.min(all_scores)), float(np.percentile(all_scores, 1))) - 10
    y_max = max(float(np.max(all_scores)), float(np.percentile(all_scores, 99))) + 13
    ax.set_ylim(max(0, y_min), y_max)

    for x, n in zip(positions, summary["n"]):
        ax.text(x, ax.get_ylim()[0] + 3, f"n={int(n)}", ha="center", va="bottom", fontsize=8.8, color="#5F6D7C")

    best_row = summary[summary["group_key"] == B4_ORDER[0]].iloc[0]
    best_x = 2
    best_y = float(best_row["mean_score"])
    ax.annotate(
        f"best B4 mean\n{best_y:.2f}",
        xy=(best_x, best_y),
        xytext=(best_x + 0.55, best_y + 28),
        ha="left",
        va="center",
        fontsize=9.5,
        color="#17263A",
        arrowprops={"arrowstyle": "->", "color": "#17263A", "lw": 1.1},
    )

    legend_handles = [
        Patch(facecolor="#D6DEE8", edgecolor="#687789", label="B0 no control"),
        Patch(facecolor="#F36F6F", edgecolor="#9E3632", label="Best B4 candidate"),
        Patch(facecolor="#A8D2E8", edgecolor="#477E9D", label="Other B4 candidates"),
        Line2D(
            [0],
            [0],
            color="#17263A",
            marker="o",
            linestyle="None",
            markersize=5.2,
            label="Mean +/- 95% CI",
        ),
    ]
    ax.legend(handles=legend_handles, loc="upper right", frameon=True, framealpha=0.96, edgecolor="#D9E1EA")

    fig.text(
        0.075,
        0.065,
        "Boxes show 300 repeated simulations; whisker overlay shows 95% CI of mean.",
        ha="left",
        va="center",
        fontsize=9.6,
        color="#2B3A4A",
    )

    fig.savefig(OUTPUT_PNG, dpi=220)
    fig.savefig(OUTPUT_SVG)
    plt.close(fig)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    clean = _read_clean_source()
    summary = _build_summary(clean)
    clean.to_csv(OUTPUT_CLEAN, index=False)
    summary.to_csv(OUTPUT_SUMMARY, index=False)
    _draw_plot(clean, summary)

    print(f"Clean rows: {len(clean)}")
    print(summary[["display_label", "n", "mean_score", "std_score", "ci95_low", "ci95_high"]].round(3).to_string(index=False))
    print(f"Saved: {OUTPUT_PNG}")
    print(f"Saved: {OUTPUT_SVG}")
    print(f"Saved: {OUTPUT_SUMMARY}")
    print(f"Saved: {OUTPUT_CLEAN}")


if __name__ == "__main__":
    main()
