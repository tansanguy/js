#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT = PROJECT_ROOT / "gcpcsv/final result/clean/15seed16theta.csv"
OUTPUT_DIR = PROJECT_ROOT / "results/figures/seed_repeat_variance"

NAVY = "#0B1F3A"
BLUE = "#2F80C5"
TEAL = "#2A9D78"
ORANGE = "#D47845"
GOLD = "#F2A541"
GRID = "#D9E2EC"
TEXT = "#25364A"


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


def load_data() -> pd.DataFrame:
    df = pd.read_csv(INPUT)
    numeric = [
        "theta_rank",
        "repeat_id",
        "objective_score",
        "D_E_sec",
        "D_G_sec",
        "t_lead",
        "delta_T_thr",
        "G_ext",
        "Q_ratio",
        "tau",
    ]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df[df["final_status"].astype(str).str.upper().eq("PASS")].copy()
    df = df[df["objective_score"].notna()].copy()
    df["theta_label"] = (
        "θ"
        + df["theta_rank"].astype(int).astype(str)
        + "\n"
        + df["parameter_id"].astype(str).str.extract(r"(r\d+_\d+)")[0].fillna("")
    )
    return df


def summarize(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    theta = (
        df.groupby(["parameter_id", "theta_label"], sort=False)
        .agg(
            theta_rank=("theta_rank", "first"),
            t_lead=("t_lead", "first"),
            delta_T_thr=("delta_T_thr", "first"),
            G_ext=("G_ext", "first"),
            Q_ratio=("Q_ratio", "first"),
            tau=("tau", "first"),
            repeat_count=("objective_score", "size"),
            mean_score=("objective_score", "mean"),
            std_score=("objective_score", "std"),
            var_score=("objective_score", "var"),
            min_score=("objective_score", "min"),
            max_score=("objective_score", "max"),
            mean_delay_A=("D_E_sec", "mean"),
            mean_delay_N=("D_G_sec", "mean"),
        )
        .reset_index()
        .sort_values(["mean_score", "std_score"], kind="mergesort")
        .reset_index(drop=True)
    )
    theta["plot_rank"] = np.arange(1, len(theta) + 1)

    seed = (
        df.groupby("repeat_id", sort=True)
        .agg(
            theta_count=("objective_score", "size"),
            mean_score=("objective_score", "mean"),
            std_score=("objective_score", "std"),
            var_score=("objective_score", "var"),
            min_score=("objective_score", "min"),
            max_score=("objective_score", "max"),
        )
        .reset_index()
    )
    return theta, seed


def plot_theta_variance(df: pd.DataFrame, theta: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(12.2, 6.2), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    lookup = theta.set_index("parameter_id")["plot_rank"].to_dict()
    df = df.copy()
    df["plot_rank"] = df["parameter_id"].map(lookup)
    jitter = ((df["repeat_id"] % 7) - 3) * 0.025

    ax.scatter(
        df["plot_rank"] + jitter,
        df["objective_score"],
        s=14,
        color=NAVY,
        alpha=0.28,
        linewidth=0,
        label="15 seed observations",
        zorder=2,
    )
    ax.errorbar(
        theta["plot_rank"],
        theta["mean_score"],
        yerr=theta["std_score"],
        fmt="o-",
        color=BLUE,
        ecolor="#AFCBE4",
        elinewidth=2.0,
        capsize=4,
        markersize=5.5,
        linewidth=2.3,
        label="mean ± 1 std",
        zorder=4,
    )

    best = theta.iloc[0]
    ax.scatter(
        [best["plot_rank"]],
        [best["mean_score"]],
        s=92,
        color=GOLD,
        edgecolor=NAVY,
        linewidth=1.2,
        label="best mean",
        zorder=6,
    )
    ax.annotate(
        f"best mean\n{best['mean_score']:.2f}",
        xy=(best["plot_rank"], best["mean_score"]),
        xytext=(best["plot_rank"] + 1.1, best["mean_score"] - 22),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.1),
        fontsize=9.5,
        color=NAVY,
        ha="left",
        va="center",
    )

    ax.set_xlim(0.4, len(theta) + 0.6)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    ax.set_title("θ 후보별 반복 성능과 분산", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "16개 θ를 각각 15회 반복 평가 · 점=개별 seed 결과, 선=평균 Score, 에러바=±1 표준편차 · Score 낮을수록 우수",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.5,
    )
    ax.set_xlabel("θ 후보(평균 Score 기준 정렬)")
    ax.set_ylabel("Objective Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)
    fig.savefig(OUTPUT_DIR / "theta_15seed_mean_variance.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "theta_15seed_mean_variance.svg", bbox_inches="tight")
    plt.close(fig)


def plot_seed_variance(seed: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(10.8, 5.9), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    x = seed["repeat_id"].to_numpy(dtype=float)
    y = seed["mean_score"].to_numpy(dtype=float)
    std = seed["std_score"].to_numpy(dtype=float)
    ax.fill_between(x, y - std, y + std, color=TEAL, alpha=0.16, linewidth=0, label="±1 std across 16 θ")
    ax.plot(x, y, color=TEAL, lw=2.4, marker="o", markersize=5, label="seed mean score")
    ax.scatter(x, seed["min_score"], s=20, color=BLUE, alpha=0.75, label="best θ in seed", zorder=4)

    best_seed = seed.loc[seed["mean_score"].idxmin()]
    ax.scatter(
        [best_seed["repeat_id"]],
        [best_seed["mean_score"]],
        s=86,
        color=GOLD,
        edgecolor=NAVY,
        linewidth=1.15,
        zorder=6,
        label="best seed mean",
    )
    ax.annotate(
        f"lowest seed mean\nseed {int(best_seed['repeat_id'])} · {best_seed['mean_score']:.2f}",
        xy=(best_seed["repeat_id"], best_seed["mean_score"]),
        xytext=(best_seed["repeat_id"] + 1.3, best_seed["mean_score"] - 18),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.1),
        fontsize=9.5,
        color=NAVY,
        ha="left",
        va="center",
    )

    ax.set_xlim(0.5, 15.5)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    ax.set_title("시드 반복별 평균 성능과 분산", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "repeat_id를 seed 반복으로 해석 · 각 seed에서 16개 θ의 평균 Score와 산포를 표시 · Score 낮을수록 우수",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.5,
    )
    ax.set_xlabel("Seed repeat_id")
    ax.set_ylabel("Objective Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)
    fig.savefig(OUTPUT_DIR / "seed_repeat_mean_variance.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "seed_repeat_mean_variance.svg", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    configure_style()
    df = load_data()
    theta, seed = summarize(df)
    df.to_csv(OUTPUT_DIR / "15seed16theta_pass_only_used.csv", index=False)
    theta.to_csv(OUTPUT_DIR / "theta_15seed_summary.csv", index=False)
    seed.to_csv(OUTPUT_DIR / "seed_repeat_summary.csv", index=False)
    plot_theta_variance(df, theta)
    plot_seed_variance(seed)


if __name__ == "__main__":
    main()
