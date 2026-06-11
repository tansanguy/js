#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT = PROJECT_ROOT / "gcpcsv/final result/clean/BO.csv"
OUTPUT_DIR = PROJECT_ROOT / "results/figures/optimization_comparison"

NAVY = "#0B1F3A"
BLUE = "#2F80C5"
LIGHT_BLUE = "#DCECF8"
GRID = "#D9E2EC"
TEXT = "#25364A"
ORANGE = "#F2A541"
MUTED = "#6B7788"


def configure_style() -> None:
    for candidate in [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    ]:
        if Path(candidate).is_file():
            fm.fontManager.addfont(candidate)
            plt.rcParams["font.family"] = fm.FontProperties(fname=candidate).get_name()
            break
    plt.rcParams.update(
        {
            "figure.dpi": 160,
            "savefig.dpi": 260,
            "axes.unicode_minus": False,
            "axes.edgecolor": "#C7D1DC",
            "axes.labelcolor": TEXT,
            "xtick.color": "#6B7788",
            "ytick.color": "#6B7788",
            "axes.titlecolor": NAVY,
        }
    )


def load_valid_bo() -> pd.DataFrame:
    df = pd.read_csv(INPUT)
    for col in ["round", "round_theta_index", "global_eval_index", "score", "penalty", "D_E_sec", "D_G_sec"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    valid = (
        df["final_status"].astype(str).str.upper().eq("PASS")
        & df["penalty"].fillna(0).eq(0)
        & df["score"].notna()
        & df["score"].lt(10000)
    )
    return df.loc[valid].sort_values(["round", "round_theta_index"], kind="mergesort").copy()


def make_round_summary(valid: pd.DataFrame) -> pd.DataFrame:
    max_round = int(valid["round"].max())
    grouped = (
        valid.groupby("round", sort=True)
        .agg(
            pass_theta_count=("score", "size"),
            round_best_score=("score", "min"),
            round_mean_score=("score", "mean"),
            round_std_score=("score", "std"),
            round_min_delay_A=("D_E_sec", "min"),
            round_mean_delay_N=("D_G_sec", "mean"),
        )
        .reset_index()
    )

    rounds = pd.DataFrame({"round": np.arange(1, max_round + 1)})
    summary = rounds.merge(grouped, on="round", how="left")
    summary["best_so_far_score"] = summary["round_best_score"].cummin().ffill()
    summary["has_pass"] = summary["round_best_score"].notna()
    return summary


def plot(valid: pd.DataFrame, summary: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11.6, 6.4), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    rng = np.random.default_rng(42)
    jitter = rng.uniform(-0.12, 0.12, len(valid))
    ax.scatter(
        valid["round"] + jitter,
        valid["score"],
        s=18,
        color=NAVY,
        alpha=0.22,
        linewidth=0,
        label="PASS θ observations",
        zorder=2,
    )

    observed_rounds = summary[summary["has_pass"]].copy()
    ax.plot(
        observed_rounds["round"],
        observed_rounds["round_best_score"],
        color="#8BB8DD",
        lw=1.65,
        marker="o",
        markersize=4.5,
        alpha=0.72,
        label="round-best score",
        zorder=3,
    )
    ax.step(
        summary["round"],
        summary["best_so_far_score"],
        where="post",
        color=BLUE,
        lw=3.0,
        label="best-so-far convergence",
        zorder=5,
    )

    improvement = summary["best_so_far_score"].diff().lt(0)
    improvement.iloc[0] = summary["best_so_far_score"].notna().iloc[0]
    improved = summary[improvement & summary["best_so_far_score"].notna()]
    ax.scatter(
        improved["round"],
        improved["best_so_far_score"],
        s=54,
        color=BLUE,
        edgecolor="white",
        linewidth=1.0,
        zorder=6,
        label="new incumbent",
    )

    best = observed_rounds.loc[observed_rounds["round_best_score"].idxmin()]
    ax.scatter(
        [best["round"]],
        [best["round_best_score"]],
        s=112,
        color=ORANGE,
        edgecolor=NAVY,
        linewidth=1.35,
        label="best observed",
        zorder=8,
    )
    ax.annotate(
        f"best observed\nR{int(best['round'])} · {best['round_best_score']:.2f}",
        xy=(best["round"], best["round_best_score"]),
        xytext=(best["round"] + 5.2, best["round_best_score"] + 24),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.25),
        fontsize=10.5,
        color=NAVY,
        ha="left",
        va="center",
    )

    fail_only = summary[~summary["has_pass"]]
    for r in fail_only["round"]:
        ax.axvline(r, color="#EFF3F7", lw=0.7, zorder=0)
    ax.text(
        0.985,
        0.04,
        f"PASS {len(valid)} / total 300 · fail/penalty excluded",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=9.5,
        color=MUTED,
    )

    finite_scores = np.concatenate(
        [
            valid["score"].to_numpy(dtype=float),
            summary["best_so_far_score"].dropna().to_numpy(dtype=float),
        ]
    )
    ax.set_ylim(np.nanpercentile(finite_scores, 2) - 18, np.nanpercentile(finite_scores, 94) + 36)
    ax.set_xlim(1, 50)
    ax.xaxis.set_major_locator(MaxNLocator(9, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    ax.set_title("베이지안 최적화 수렴 그래프", loc="left", fontsize=19, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "x축=BO 라운드 · y축=Score(낮을수록 우수) · FAIL/penalty 후보는 제외하고 정상 평가(PASS)만 반영",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.7,
    )
    ax.set_xlabel("BO 라운드")
    ax.set_ylabel("Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)

    fig.savefig(OUTPUT_DIR / "bo_clean_convergence_fail_excluded.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "bo_clean_convergence_fail_excluded.svg", bbox_inches="tight")
    plt.close(fig)


def plot_polished(summary: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11.6, 6.35), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    observed = summary[summary["has_pass"]].copy()
    y_focus_top = 382
    observed["round_best_clipped"] = observed["round_best_score"].clip(upper=y_focus_top)

    ax.plot(
        observed["round"],
        observed["round_best_clipped"],
        color="#A8CBE8",
        lw=1.85,
        marker="o",
        markersize=5.0,
        alpha=0.9,
        label="round-best score",
        zorder=3,
    )
    clipped = observed[observed["round_best_score"] > y_focus_top]
    ax.scatter(
        clipped["round"],
        np.full(len(clipped), y_focus_top),
        marker="^",
        s=64,
        color="#A8CBE8",
        edgecolor="white",
        linewidth=0.8,
        zorder=4,
        label="round-best above view",
    )

    ax.step(
        summary["round"],
        summary["best_so_far_score"],
        where="post",
        color=BLUE,
        lw=3.4,
        label="best-so-far convergence",
        zorder=5,
    )

    improvement = summary["best_so_far_score"].diff().lt(0)
    improvement.iloc[0] = summary["best_so_far_score"].notna().iloc[0]
    improved = summary[improvement & summary["best_so_far_score"].notna()]
    ax.scatter(
        improved["round"],
        improved["best_so_far_score"],
        s=58,
        color=BLUE,
        edgecolor="white",
        linewidth=1.15,
        zorder=6,
        label="new incumbent",
    )

    best = observed.loc[observed["round_best_score"].idxmin()]
    ax.scatter(
        [best["round"]],
        [best["round_best_score"]],
        s=122,
        color=ORANGE,
        edgecolor=NAVY,
        linewidth=1.4,
        label="best observed",
        zorder=8,
    )
    ax.annotate(
        f"최적 관측값\nR{int(best['round'])} · {best['round_best_score']:.2f}",
        xy=(best["round"], best["round_best_score"]),
        xytext=(best["round"] + 5.4, best["round_best_score"] + 27),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.25),
        fontsize=10.5,
        color=NAVY,
        ha="left",
        va="center",
    )

    ax.set_ylim(232, y_focus_top + 5)
    ax.set_xlim(1, 50)
    ax.xaxis.set_major_locator(MaxNLocator(9, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    ax.set_title("베이지안 최적화 수렴 그래프", loc="left", fontsize=19, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "x축=BO 라운드 · y축=Score(낮을수록 우수) · FAIL/penalty 제외 · 굵은 선은 현재까지 발견한 최저 Score(best-so-far)",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.7,
    )
    ax.set_xlabel("BO 라운드")
    ax.set_ylabel("Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)

    fig.savefig(OUTPUT_DIR / "bo_clean_convergence_fail_excluded_polished.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "bo_clean_convergence_fail_excluded_polished.svg", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    configure_style()
    valid = load_valid_bo()
    summary = make_round_summary(valid)
    valid.to_csv(OUTPUT_DIR / "bo_clean_convergence_pass_only_points.csv", index=False)
    summary.to_csv(OUTPUT_DIR / "bo_clean_convergence_round_summary.csv", index=False)
    plot(valid, summary)
    plot_polished(summary)


if __name__ == "__main__":
    main()
