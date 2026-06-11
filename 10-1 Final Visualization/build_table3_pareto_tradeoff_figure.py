#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT = PROJECT_ROOT / "gcpcsv/final result/gcp_min_tradeoff_table3_pareto.csv"
OUTPUT_DIR = PROJECT_ROOT / "results/figures/weight_sensitivity_table3"

NAVY = "#0B1F3A"
BLUE = "#2F80C5"
LIGHT_BLUE = "#A9CDEC"
ORANGE = "#D65F38"
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


def smooth_curve(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    try:
        from scipy.interpolate import PchipInterpolator

        xs = np.linspace(float(x.min()), float(x.max()), 240)
        ys = PchipInterpolator(x, y)(xs)
        return xs, ys
    except Exception:
        xs = np.linspace(float(x.min()), float(x.max()), 240)
        ys = np.interp(xs, x, y)
        return xs, ys


def plot() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(INPUT)
    for col in ["D_E_sec", "D_G_sec", "Z_sec", "t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("D_G_sec", kind="mergesort").reset_index(drop=True)
    df.to_csv(OUTPUT_DIR / "gcp_min_tradeoff_table3_pareto_used.csv", index=False)

    x = df["D_G_sec"].to_numpy(dtype=float)
    y = df["D_E_sec"].to_numpy(dtype=float)
    xs, ys = smooth_curve(x, y)
    knee = df[df["is_knee"].astype(bool)].copy()
    if knee.empty:
        knee = df.iloc[[len(df) // 2]].copy()

    fig, ax = plt.subplots(figsize=(10.6, 6.4), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    ax.plot(xs, ys, color=LIGHT_BLUE, lw=2.2, alpha=0.95, zorder=2)
    ax.scatter(
        df["D_G_sec"],
        df["D_E_sec"],
        s=78,
        color=BLUE,
        edgecolor="white",
        linewidth=1.15,
        label="가중치별 최적해",
        zorder=4,
    )
    ax.scatter(
        knee["D_G_sec"],
        knee["D_E_sec"],
        s=168,
        color=ORANGE,
        edgecolor="white",
        linewidth=1.3,
        label=f"무릎점 {knee.iloc[0]['weight_ratio']}",
        zorder=6,
    )

    label_offsets = {
        "3:1": (-2, -14),
        "1:1": (5, 10),
        "1:2": (5, -4),
        "1:3": (-18, 8),
        "1:5": (5, 10),
        "1:10": (5, -2),
        "1:20": (-12, -16),
    }
    for _, row in df.iterrows():
        dx, dy = label_offsets.get(str(row["weight_ratio"]), (4, 5))
        color = ORANGE if bool(row["is_knee"]) else NAVY
        weight = "bold" if bool(row["is_knee"]) else "normal"
        ax.annotate(
            str(row["weight_ratio"]),
            xy=(row["D_G_sec"], row["D_E_sec"]),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=9.3,
            color=color,
            fontweight=weight,
            ha="left" if dx >= 0 else "right",
            va="center",
        )

    x_pad = max((x.max() - x.min()) * 0.09, 10)
    y_pad = max((y.max() - y.min()) * 0.08, 18)
    ax.set_xlim(x.min() - x_pad, x.max() + x_pad)
    ax.set_ylim(y.min() - y_pad, y.max() + y_pad)
    ax.xaxis.set_major_locator(MaxNLocator(7))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, color=GRID, linewidth=0.85, alpha=0.78)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")

    ax.set_title("가중치 민감도 분석", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "각 점 = 해당 가중치로 다시 최적화한 결과 · 왼쪽 아래일수록 양쪽 지연 모두 작음 · 붉은 점은 무릎점 후보",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.3,
    )
    ax.set_xlabel("일반차 평균 지연  D_G (s)")
    ax.set_ylabel("긴급차 지연  D_E (s)")
    ax.legend(
        loc="upper left",
        bbox_to_anchor=(0, 0.98),
        frameon=False,
        ncol=2,
        handletextpad=0.4,
        columnspacing=1.4,
    )

    fig.savefig(OUTPUT_DIR / "gcp_min_tradeoff_table3_pareto.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "gcp_min_tradeoff_table3_pareto.svg", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    configure_style()
    plot()


if __name__ == "__main__":
    main()
