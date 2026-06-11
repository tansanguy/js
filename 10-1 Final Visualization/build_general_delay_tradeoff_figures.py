#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.lines import Line2D


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CURRENT_CSV = PROJECT_ROOT / "gcpcsv/final result/CURRENT_RUN_GENERAL_DELAY_IMPROVERS_high_emergency_damage.csv"
GLOBAL_CSV = PROJECT_ROOT / "gcpcsv/final result/GENERAL_DELAY_IMPROVERS_high_emergency_damage.csv"
OUTPUT_DIR = PROJECT_ROOT / "results/figures/general_delay_tradeoff"

NAVY = "#0B1F3A"
BLUE = "#2F80C5"
RED = "#C95F4A"
ORANGE = "#F2A541"
GREEN = "#2E8B57"
GRID = "#D9E2EC"
TEXT = "#25364A"

ANCHOR = {
    "role": "emergency anchor",
    "parameter_id": "bo_r27_004_tl94_dt24_ge15_qr28_tau79",
    "theta_short": "tl94 dt24 ge15 qr28 tau79",
    "D_E_sec": 195.92,
    "D_G_sec": 466.68,
    "color": GREEN,
}

HIGHLIGHT_IDS = [
    "bo_r27_004_tl94_dt24_ge15_qr28_tau79",
    "bo_r03_003_tl13_dt11_ge30_qr44_tau82",
    "bo_r03_005_tl51_dt205_ge5_qr97_tau89",
    "bo_r03_007_tl106_dt201_ge47_qr60_tau74",
]

ROLE_BY_ID = {
    "bo_r27_004_tl94_dt24_ge15_qr28_tau79": "emergency anchor",
    "bo_r03_003_tl13_dt11_ge30_qr44_tau82": "moderate general gain",
    "bo_r03_005_tl51_dt205_ge5_qr97_tau89": "strong general gain",
    "bo_r03_007_tl106_dt201_ge47_qr60_tau74": "extreme general gain",
}

COLOR_BY_ID = {
    "bo_r27_004_tl94_dt24_ge15_qr28_tau79": GREEN,
    "bo_r03_003_tl13_dt11_ge30_qr44_tau82": BLUE,
    "bo_r03_005_tl51_dt205_ge5_qr97_tau89": ORANGE,
    "bo_r03_007_tl106_dt201_ge47_qr60_tau74": RED,
}


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


def theta_short(row: pd.Series) -> str:
    return (
        f"tl{int(row.t_lead)} dt{int(row.delta_T_thr)} ge{int(row.G_ext)} "
        f"qr{int(round(float(row.Q_ratio) * 100))} tau{int(round(float(row.tau) * 100))}"
    )


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    current = pd.read_csv(CURRENT_CSV)
    global_df = pd.read_csv(GLOBAL_CSV)
    current["source_group"] = "current tradeoff run"
    global_df["source_group"] = "all collected improvers"
    for df in (current, global_df):
        df["theta_short"] = df.apply(theta_short, axis=1)
        df["general_saved_sec"] = ANCHOR["D_G_sec"] - df["D_G_sec"]
        df["emergency_damage_sec"] = df["D_E_sec"] - ANCHOR["D_E_sec"]

    anchor = pd.DataFrame([ANCHOR])
    anchor["source_group"] = "anchor"
    anchor["t_lead"] = 94
    anchor["delta_T_thr"] = 24
    anchor["G_ext"] = 15
    anchor["Q_ratio"] = 0.28
    anchor["tau"] = 0.79
    anchor["general_saved_sec"] = 0.0
    anchor["emergency_damage_sec"] = 0.0

    highlights = pd.concat(
        [
            anchor,
            current[current["parameter_id"].isin(HIGHLIGHT_IDS[1:])],
        ],
        ignore_index=True,
        sort=False,
    )
    highlights["role"] = highlights["parameter_id"].map(ROLE_BY_ID)
    highlights["color"] = highlights["parameter_id"].map(COLOR_BY_ID)
    highlights = highlights.set_index("parameter_id").loc[HIGHLIGHT_IDS].reset_index()
    return current, global_df, highlights


def write_summary(highlights: pd.DataFrame) -> None:
    cols = [
        "role",
        "parameter_id",
        "theta_short",
        "D_E_sec",
        "D_G_sec",
        "general_saved_sec",
        "emergency_damage_sec",
    ]
    highlights[cols].to_csv(OUTPUT_DIR / "general_delay_tradeoff_highlight_candidates.csv", index=False)


def plot_gain_damage(current: pd.DataFrame, global_df: pd.DataFrame, highlights: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11.6, 7.0), constrained_layout=True)
    fig.patch.set_facecolor("#EEF5FB")
    ax.set_facecolor("#EEF5FB")
    ax.grid(True, color=GRID, linewidth=0.9)
    ax.spines[["top", "right"]].set_visible(False)

    ax.scatter(
        global_df["general_saved_sec"],
        global_df["emergency_damage_sec"],
        s=20,
        color="#A8B7C7",
        alpha=0.28,
        edgecolor="none",
        label="전체 general-delay 개선 후보",
        zorder=1,
    )
    ax.scatter(
        current["general_saved_sec"],
        current["emergency_damage_sec"],
        s=42,
        color="#5B8DB8",
        alpha=0.48,
        edgecolor="white",
        linewidth=0.5,
        label="현재 local tradeoff run 후보",
        zorder=2,
    )

    ax.plot(
        highlights["general_saved_sec"],
        highlights["emergency_damage_sec"],
        color=NAVY,
        linewidth=2.4,
        alpha=0.82,
        zorder=3,
    )
    for _, row in highlights.iterrows():
        ax.scatter(
            row["general_saved_sec"],
            row["emergency_damage_sec"],
            s=190,
            color=row["color"],
            edgecolor="white",
            linewidth=1.8,
            zorder=4,
        )

    offsets = {
        "emergency anchor": (8, 12, "left", "bottom"),
        "moderate general gain": (8, 12, "left", "bottom"),
        "strong general gain": (9, -16, "left", "top"),
        "extreme general gain": (-10, 10, "right", "bottom"),
    }
    for _, row in highlights.iterrows():
        dx, dy, ha, va = offsets[row["role"]]
        ax.annotate(
            f"{row['role']}\nD_G {row['D_G_sec']:.0f}s, D_E {row['D_E_sec']:.0f}s",
            xy=(row["general_saved_sec"], row["emergency_damage_sec"]),
            xytext=(dx, dy),
            textcoords="offset points",
            ha=ha,
            va=va,
            color=NAVY,
            fontsize=10.2,
            fontweight="bold" if row["role"] in {"emergency anchor", "extreme general gain"} else "normal",
            zorder=5,
        )

    ax.axhline(0, color="#8FA2B5", linewidth=1.0, alpha=0.8)
    ax.axvline(0, color="#8FA2B5", linewidth=1.0, alpha=0.8)
    ax.set_title("일반차 지연 개선 후보를 섞어 본 명확한 trade-off", loc="left", fontsize=18.5, fontweight="bold", pad=18)
    ax.text(
        0,
        0.974,
        "오른쪽으로 갈수록 일반차 지연은 줄지만, 위로 갈수록 응급차 지연 손실이 커짐",
        transform=ax.transAxes,
        color="#5F7185",
        fontsize=10.8,
        va="top",
    )
    ax.set_xlabel("일반차 지연 절감량 vs emergency anchor (s)", fontsize=11.8)
    ax.set_ylabel("응급차 지연 악화량 vs emergency anchor (s)", fontsize=11.8)
    ax.set_xlim(-12, max(185, float(highlights["general_saved_sec"].max()) + 22))
    ax.set_ylim(-25, max(430, float(highlights["emergency_damage_sec"].max()) + 35))
    ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.93), frameon=True, facecolor="white", edgecolor="#D9E2EC", framealpha=0.96)
    ax.text(
        0,
        -0.13,
        "강조선은 발표용 대표 후보 4개만 연결. 배경점은 CSV의 실제 후보 분포이며 Top5 최적 대표해가 아님.",
        transform=ax.transAxes,
        color="#7A8797",
        fontsize=9.8,
    )
    fig.savefig(OUTPUT_DIR / "general_delay_tradeoff_gain_damage.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "general_delay_tradeoff_gain_damage.svg", bbox_inches="tight")
    plt.close(fig)


def plot_raw_delays(current: pd.DataFrame, global_df: pd.DataFrame, highlights: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11.2, 6.8), constrained_layout=True)
    fig.patch.set_facecolor("#F6F9FC")
    ax.set_facecolor("#F6F9FC")
    ax.grid(True, color=GRID, linewidth=0.9)
    ax.spines[["top", "right"]].set_visible(False)

    ax.scatter(global_df["D_G_sec"], global_df["D_E_sec"], s=18, color="#AAB8C8", alpha=0.25, edgecolor="none")
    ax.scatter(current["D_G_sec"], current["D_E_sec"], s=42, color="#5B8DB8", alpha=0.50, edgecolor="white", linewidth=0.5)
    ax.plot(highlights["D_G_sec"], highlights["D_E_sec"], color=NAVY, linewidth=2.2, alpha=0.8)

    for _, row in highlights.iterrows():
        ax.scatter(row["D_G_sec"], row["D_E_sec"], s=180, color=row["color"], edgecolor="white", linewidth=1.7, zorder=4)
        ax.annotate(
            f"{row['role']}\n({row['D_G_sec']:.0f}, {row['D_E_sec']:.0f})",
            xy=(row["D_G_sec"], row["D_E_sec"]),
            xytext=(8 if row["role"] != "extreme general gain" else -10, 10),
            textcoords="offset points",
            ha="left" if row["role"] != "extreme general gain" else "right",
            color=NAVY,
            fontsize=10.0,
        )

    ax.invert_xaxis()
    ax.set_title("D_G 감소와 D_E 증가가 동시에 보이는 후보 축", loc="left", fontsize=18.5, fontweight="bold", pad=18)
    ax.text(
        0,
        0.974,
        "x축은 일반차 지연 D_G, y축은 응급차 지연 D_E. 왼쪽으로 갈수록 일반차는 좋아지고 응급차는 나빠짐.",
        transform=ax.transAxes,
        color="#5F7185",
        fontsize=10.6,
        va="top",
    )
    ax.set_xlabel("일반차 평균 지연 D_G (s) - 낮을수록 좋음", fontsize=11.8)
    ax.set_ylabel("응급차 지연 D_E (s) - 낮을수록 좋음", fontsize=11.8)
    handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor="#AAB8C8", markeredgecolor="none", alpha=0.5, markersize=7, label="전체 후보"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor="#5B8DB8", markeredgecolor="white", markersize=8, label="현재 run 후보"),
        Line2D([0], [0], color=NAVY, lw=2.2, label="발표용 trade-off 축"),
    ]
    ax.legend(handles=handles, loc="upper right", frameon=True, facecolor="white", edgecolor="#D9E2EC", framealpha=0.96)
    fig.savefig(OUTPUT_DIR / "general_delay_tradeoff_raw_delays.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "general_delay_tradeoff_raw_delays.svg", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    configure_style()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    current, global_df, highlights = load_data()
    write_summary(highlights)
    plot_gain_damage(current, global_df, highlights)
    plot_raw_delays(current, global_df, highlights)
    print(f"Wrote {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
