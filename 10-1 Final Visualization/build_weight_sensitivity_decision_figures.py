#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_CSV = PROJECT_ROOT / "gcpcsv/final result/MUST_SEE_WeightSensitivity_G1_Evar.csv"
INPUT_MD = PROJECT_ROOT / "gcpcsv/final result/MUST_SEE_WeightSensitivity_G1_Evar_analysis.md"
OUTPUT_DIR = PROJECT_ROOT / "results/figures/weight_sensitivity_decision"
KNEE_CANDIDATE_RATIO = "1:2"

NAVY = "#0B1F3A"
BLUE = "#2F80C5"
RED = "#C95F4A"
GRID = "#D9E2EC"
TEXT = "#25364A"


def configure_style() -> None:
    font_candidates = [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    ]
    for candidate in font_candidates:
        if Path(candidate).is_file():
            fm.fontManager.addfont(candidate)
            plt.rcParams["font.family"] = fm.FontProperties(fname=candidate).get_name()
            break
    plt.rcParams.update(
        {
            "figure.dpi": 160,
            "savefig.dpi": 240,
            "axes.unicode_minus": False,
            "axes.edgecolor": "#C7D1DC",
            "axes.labelcolor": TEXT,
            "xtick.color": "#6B7788",
            "ytick.color": "#6B7788",
            "axes.titlecolor": NAVY,
        }
    )


def parse_ratio_right(ratio: str) -> int:
    return int(str(ratio).split(":")[1])


def theta_tuple(row: pd.Series) -> str:
    return (
        f"({int(row.t_lead)}, {int(row.delta_T_thr)}, {int(row.G_ext)}, "
        f"{float(row.Q_ratio):.2f}, {float(row.tau):.2f})"
    )


def pareto_efficient(df: pd.DataFrame) -> pd.Series:
    values = df[["D_G_sec", "D_E_sec"]].to_numpy(dtype=float)
    efficient = np.ones(len(values), dtype=bool)
    for idx, point in enumerate(values):
        dominated = np.all(values <= point, axis=1) & np.any(values < point, axis=1)
        efficient[idx] = not np.any(dominated)
    return pd.Series(efficient, index=df.index)


def load_data() -> pd.DataFrame:
    df = pd.read_csv(INPUT_CSV)
    df["ratio_order"] = df["weight_ratio_general_emergency"].map(parse_ratio_right)
    df["theta"] = df.apply(theta_tuple, axis=1)
    df["is_pareto_efficient"] = pareto_efficient(df)
    df["point_role"] = "가중치별 대표해"
    df.loc[df["weight_ratio_general_emergency"] == KNEE_CANDIDATE_RATIO, "point_role"] = "무릎점 후보"
    return df.sort_values("ratio_order").reset_index(drop=True)


def write_decision_table(df: pd.DataFrame) -> pd.DataFrame:
    table = pd.DataFrame(
        {
            "가중치(w_G:w_E)": df["weight_ratio_general_emergency"],
            "최적 θ = (t_lead, delta_T_thr, G_ext, Q_ratio, tau)": df["theta"],
            "delay_A_긴급차_sec": df["D_E_sec"].round(2),
            "delay_N_일반차_sec": df["D_G_sec"].round(2),
            "rounds_completed": df["rounds_completed"],
            "SPC_stop_round": df["spc_stop_round"],
            "pareto_efficient": df["is_pareto_efficient"],
            "note": np.where(
                df["weight_ratio_general_emergency"] == KNEE_CANDIDATE_RATIO,
                "무릎점 후보 표시용; 채택 결론 아님",
                "",
            ),
        }
    )
    table.to_csv(OUTPUT_DIR / "weight_sensitivity_decision_table.csv", index=False)
    return table


def plot_pareto(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11.4, 7.0), constrained_layout=True)
    fig.patch.set_facecolor("#F5F8FB")
    ax.set_facecolor("#F5F8FB")

    ordered = df.sort_values("weight_E")
    ax.plot(
        ordered["D_G_sec"],
        ordered["D_E_sec"],
        color="#7CB5E7",
        linewidth=1.8,
        alpha=0.52,
        linestyle="--",
        zorder=1,
    )

    normal = df[df["weight_ratio_general_emergency"] != KNEE_CANDIDATE_RATIO]
    ax.scatter(
        normal["D_G_sec"],
        normal["D_E_sec"],
        s=92,
        color=BLUE,
        edgecolor="white",
        linewidth=1.4,
        zorder=3,
        label="가중치별 대표해",
    )

    dominated = df[~df["is_pareto_efficient"]]
    ax.scatter(
        dominated["D_G_sec"],
        dominated["D_E_sec"],
        s=112,
        facecolor="none",
        edgecolor="#8FA2B5",
        linewidth=1.1,
        zorder=4,
        label="비지배 해 아님",
    )

    knee = df[df["weight_ratio_general_emergency"] == KNEE_CANDIDATE_RATIO].iloc[0]
    ax.scatter(
        [knee["D_G_sec"]],
        [knee["D_E_sec"]],
        s=190,
        color=RED,
        edgecolor="white",
        linewidth=1.6,
        zorder=5,
        label=f"무릎점 후보 {KNEE_CANDIDATE_RATIO}",
    )

    for _, row in df.iterrows():
        label = row["weight_ratio_general_emergency"]
        dx = 0.22
        dy = 0.95
        if label == "1:1":
            dx, dy = 0.12, -2.1
        elif label == "1:2":
            dx, dy = 0.15, 1.2
        elif label == "1:20":
            dx, dy = 0.14, 1.1
        ax.text(row["D_G_sec"] + dx, row["D_E_sec"] + dy, label, color=TEXT, fontsize=10, fontweight="bold")

    ax.annotate(
        "무릎점 후보\n채택 결론 아님",
        xy=(knee["D_G_sec"], knee["D_E_sec"]),
        xytext=(knee["D_G_sec"] + 4.2, knee["D_E_sec"] - 12),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.2),
        fontsize=10.3,
        color=NAVY,
        ha="left",
        va="center",
    )

    xpad = max((df["D_G_sec"].max() - df["D_G_sec"].min()) * 0.22, 2)
    ypad = max((df["D_E_sec"].max() - df["D_E_sec"].min()) * 0.20, 5)
    ax.set_xlim(df["D_G_sec"].min() - xpad, df["D_G_sec"].max() + xpad * 1.4)
    ax.set_ylim(df["D_E_sec"].min() - ypad, df["D_E_sec"].max() + ypad)
    ax.grid(True, color=GRID, linewidth=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("가중치 민감도 분석", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "각 점은 같은 사고·수요 조건에서 w_G:w_E별로 얻은 대표해 · 왼쪽 아래일수록 두 지연 모두 작음",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.5,
    )
    ax.set_xlabel("일반차 평균 지연 delay_N (s)", fontsize=11.5)
    ax.set_ylabel("긴급차 지연 delay_A (s)", fontsize=11.5)
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)
    ax.text(
        0,
        -0.13,
        "해석: 가중치 자체를 정하는 것이 아니라, 정책 결정자가 trade-off 후보군을 보도록 펼쳐 보여주는 그림",
        transform=ax.transAxes,
        color="#7A8797",
        fontsize=9.6,
    )

    fig.savefig(OUTPUT_DIR / "weight_sensitivity_pareto_decision.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "weight_sensitivity_pareto_decision.svg", bbox_inches="tight")
    plt.close(fig)


def plot_decision_table(table: pd.DataFrame) -> None:
    display = table[
        [
            "가중치(w_G:w_E)",
            "최적 θ = (t_lead, delta_T_thr, G_ext, Q_ratio, tau)",
            "delay_A_긴급차_sec",
            "delay_N_일반차_sec",
        ]
    ].copy()
    display.columns = ["가중치\n(w_G:w_E)", "최적 θ\n(t_lead, ΔT, G_ext, Q_ratio, τ)", "delay_A\n긴급차(s)", "delay_N\n일반차(s)"]

    fig, ax = plt.subplots(figsize=(12.2, 4.0), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.axis("off")
    ax.set_title("가중치별 대표해 요약", loc="left", fontsize=17, fontweight="bold", color=NAVY, pad=18)
    ax.text(
        0,
        0.94,
        "무릎점 후보는 표시용이며, 어느 가중치를 채택해야 한다는 결론이 아님",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.2,
    )

    cell_text = display.values.tolist()
    table_artist = ax.table(
        cellText=cell_text,
        colLabels=display.columns.tolist(),
        loc="center",
        cellLoc="center",
        colLoc="center",
        colWidths=[0.12, 0.52, 0.16, 0.16],
        bbox=[0, 0.02, 1, 0.82],
    )
    table_artist.auto_set_font_size(False)
    table_artist.set_fontsize(9.4)

    for (row, col), cell in table_artist.get_celld().items():
        cell.set_edgecolor("#D7E0EA")
        cell.set_linewidth(0.8)
        if row == 0:
            cell.set_facecolor(NAVY)
            cell.get_text().set_color("white")
            cell.get_text().set_fontweight("bold")
        else:
            ratio = display.iloc[row - 1, 0]
            if ratio == KNEE_CANDIDATE_RATIO:
                cell.set_facecolor("#F7DED7")
                cell.get_text().set_color("#7C3025")
                if col == 0:
                    cell.get_text().set_fontweight("bold")
            else:
                cell.set_facecolor("#F7FAFD" if row % 2 else "white")
                cell.get_text().set_color(TEXT)

    fig.savefig(OUTPUT_DIR / "weight_sensitivity_decision_table.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "weight_sensitivity_decision_table.svg", bbox_inches="tight")
    plt.close(fig)


def plot_polished_pareto(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11.4, 6.9), constrained_layout=True)
    fig.patch.set_facecolor("#EAF2F9")
    ax.set_facecolor("#EAF2F9")

    x_min, x_max = float(df["D_G_sec"].min()), float(df["D_G_sec"].max())
    y_min, y_max = float(df["D_E_sec"].min()), float(df["D_E_sec"].max())
    ax.set_xlim(x_min - 2.8, x_max + 4.0)
    ax.set_ylim(y_min - 8.0, y_max + 8.0)

    # Presentation-only smooth guide curve. The observed points remain exact.
    start = (x_min + 0.2, y_max - 1.2)
    control_1 = (x_min + 1.9, y_max - 7.5)
    control_2 = (x_min + 3.9, y_min + 7.8)
    end = (x_max + 0.2, y_min + 3.3)
    path = MplPath(
        [start, control_1, control_2, end],
        [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4],
    )
    ax.add_patch(
        PathPatch(
            path,
            facecolor="none",
            edgecolor="#79B4E8",
            lw=3.0,
            alpha=0.72,
            capstyle="round",
            zorder=1,
        )
    )

    for _, row in df.iterrows():
        ratio = row["weight_ratio_general_emergency"]
        is_knee = ratio == KNEE_CANDIDATE_RATIO
        is_dominated = not bool(row["is_pareto_efficient"])
        face = RED if is_knee else BLUE
        edge = "white" if not is_dominated else "#7E94AA"
        size = 220 if is_knee else 142
        alpha = 1.0 if not is_dominated or is_knee else 0.72
        ax.scatter(
            row["D_G_sec"],
            row["D_E_sec"],
            s=size,
            color=face,
            edgecolor=edge,
            linewidth=2.0,
            alpha=alpha,
            zorder=4 if is_knee else 3,
        )

    label_offsets = {
        "1:1": (0.25, -2.8),
        "1:2": (0.35, 1.2),
        "1:3": (0.25, 1.2),
        "1:5": (0.50, -1.45),
        "1:7": (-0.78, 0.55),
        "1:10": (0.16, 1.65),
        "1:20": (0.44, -1.15),
    }
    for _, row in df.iterrows():
        ratio = row["weight_ratio_general_emergency"]
        dx, dy = label_offsets.get(ratio, (0.3, 0.8))
        ax.text(
            row["D_G_sec"] + dx,
            row["D_E_sec"] + dy,
            ratio,
            color=NAVY,
            fontsize=10.3,
            fontweight="bold",
            zorder=5,
        )

    knee = df[df["weight_ratio_general_emergency"] == KNEE_CANDIDATE_RATIO].iloc[0]
    ax.annotate(
        "무릎점 후보\n정책 채택 결론 아님",
        xy=(knee["D_G_sec"], knee["D_E_sec"]),
        xytext=(knee["D_G_sec"] + 4.5, knee["D_E_sec"] - 11.0),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.4),
        fontsize=10.3,
        color=NAVY,
        ha="left",
        va="center",
        zorder=6,
    )

    ax.grid(True, color="#D3E0EC", linewidth=0.95)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#B9C7D6")
    ax.set_title("가중치 민감도 분석", loc="left", fontsize=20, fontweight="bold", pad=16, color=NAVY)
    ax.text(
        0,
        1.012,
        "각 점은 같은 사고·수요 조건에서 w_G:w_E별로 얻은 대표해 · 곡선은 trade-off를 읽기 위한 시각 보조선",
        transform=ax.transAxes,
        color="#5F7185",
        fontsize=10.6,
    )
    ax.set_xlabel("일반차 평균 지연 delay_N (s)", fontsize=11.8)
    ax.set_ylabel("긴급차 지연 delay_A (s)", fontsize=11.8)

    legend_handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=BLUE, markeredgecolor="white", markeredgewidth=1.6, markersize=10, label="가중치별 대표해"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=RED, markeredgecolor="white", markeredgewidth=1.6, markersize=12, label=f"무릎점 후보 {KNEE_CANDIDATE_RATIO}"),
        Line2D([0], [0], color="#79B4E8", lw=3, label="시각 보조 곡선"),
    ]
    ax.legend(handles=legend_handles, loc="upper right", frameon=True, facecolor="white", edgecolor="#D9E2EC", framealpha=0.97)
    ax.text(
        0,
        -0.13,
        "주의: 가중치를 정하는 그림이 아니라, 정책 결정자가 후보 해의 trade-off를 비교하도록 펼쳐 보여주는 그림",
        transform=ax.transAxes,
        color="#7A8797",
        fontsize=9.8,
    )

    fig.savefig(OUTPUT_DIR / "weight_sensitivity_pareto_polished.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "weight_sensitivity_pareto_polished.svg", bbox_inches="tight")
    plt.close(fig)


def plot_must_see_source(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11.2, 6.7), constrained_layout=True)
    fig.patch.set_facecolor("#EDF5FC")
    ax.set_facecolor("#EDF5FC")

    ax.grid(True, color="#D4E1EC", linewidth=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#B8C7D6")

    # Exact observed points from MUST_SEE_WeightSensitivity_G1_Evar.csv.
    knee = df[df["weight_ratio_general_emergency"] == KNEE_CANDIDATE_RATIO].iloc[0]
    normal = df[df["weight_ratio_general_emergency"] != KNEE_CANDIDATE_RATIO]

    # Soft visual guide only; it is intentionally separate from the observed points.
    guide = PathPatch(
        MplPath(
            [
                (461.35, 224.6),
                (462.5, 216.5),
                (464.9, 203.4),
                (466.85, 196.0),
            ],
            [MplPath.MOVETO, MplPath.CURVE4, MplPath.CURVE4, MplPath.CURVE4],
        ),
        facecolor="none",
        edgecolor="#84BDEB",
        lw=3.2,
        alpha=0.70,
        capstyle="round",
        zorder=1,
    )
    ax.add_patch(guide)

    ax.scatter(
        normal["D_G_sec"],
        normal["D_E_sec"],
        s=140,
        color=BLUE,
        edgecolor="white",
        linewidth=1.7,
        alpha=0.95,
        zorder=3,
        label="가중치별 대표해",
    )
    ax.scatter(
        [knee["D_G_sec"]],
        [knee["D_E_sec"]],
        s=230,
        color=RED,
        edgecolor="white",
        linewidth=1.9,
        zorder=5,
        label=f"무릎점 후보 {KNEE_CANDIDATE_RATIO}",
    )

    label_offsets = {
        "1:1": (0.18, -2.7),
        "1:2": (0.25, 1.25),
        "1:3": (0.25, 1.25),
        "1:5": (0.30, -1.3),
        "1:7": (-0.72, 0.80),
        "1:10": (0.22, 1.7),
        "1:20": (0.52, -1.3),
    }
    for _, row in df.iterrows():
        dx, dy = label_offsets.get(row["weight_ratio_general_emergency"], (0.2, 1.0))
        ax.text(
            row["D_G_sec"] + dx,
            row["D_E_sec"] + dy,
            row["weight_ratio_general_emergency"],
            color=NAVY,
            fontsize=10.4,
            fontweight="bold",
            zorder=6,
        )

    ax.annotate(
        "무릎점 후보\n정답/채택 결론 아님",
        xy=(knee["D_G_sec"], knee["D_E_sec"]),
        xytext=(knee["D_G_sec"] + 4.15, knee["D_E_sec"] - 10.0),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.35),
        fontsize=10.1,
        color=NAVY,
        ha="left",
        va="center",
        zorder=7,
    )

    ax.set_xlim(458.8, 478.1)
    ax.set_ylim(188.0, 232.2)
    ax.set_title("가중치 민감도 분석", loc="left", fontsize=20, fontweight="bold", pad=16, color=NAVY)
    ax.text(
        0,
        1.012,
        "MUST_SEE_WeightSensitivity_G1_Evar.csv 기반 · 점은 실제 대표해, 곡선은 trade-off 설명용 보조선",
        transform=ax.transAxes,
        color="#617386",
        fontsize=10.5,
    )
    ax.set_xlabel("일반차 평균 지연 delay_N (s)", fontsize=11.6)
    ax.set_ylabel("긴급차 지연 delay_A (s)", fontsize=11.6)
    handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=BLUE, markeredgecolor="white", markeredgewidth=1.6, markersize=10, label="가중치별 대표해"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=RED, markeredgecolor="white", markeredgewidth=1.6, markersize=12, label=f"무릎점 후보 {KNEE_CANDIDATE_RATIO}"),
        Line2D([0], [0], color="#84BDEB", lw=3.2, label="시각 보조 곡선"),
    ]
    ax.legend(handles=handles, loc="upper right", frameon=True, facecolor="white", edgecolor="#D9E2EC", framealpha=0.97)
    ax.text(
        0,
        -0.13,
        "목적: 가중치를 정하는 것이 아니라 정책 결정자에게 긴급차/일반차 지연의 맞교환 후보를 펼쳐 보여주는 것",
        transform=ax.transAxes,
        color="#7A8797",
        fontsize=9.6,
    )

    fig.savefig(OUTPUT_DIR / "weight_sensitivity_must_see_source.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "weight_sensitivity_must_see_source.svg", bbox_inches="tight")
    plt.close(fig)


def write_talk_track(df: pd.DataFrame) -> None:
    knee = df[df["weight_ratio_general_emergency"] == KNEE_CANDIDATE_RATIO].iloc[0]
    text = f"""# 발표용 1줄 해석

가중치 민감도 분석은 `w_G:w_E`를 정답으로 고르는 절차가 아니라, 동일한 사고·수요 조건에서 일반차 지연과 긴급차 지연이 어떻게 맞교환되는지 후보 해를 펼쳐 보여주는 절차입니다.

## 그림 설명

- x축: 일반차 평균 지연 `delay_N = D_G_sec`
- y축: 긴급차 지연 `delay_A = D_E_sec`
- 왼쪽 아래일수록 두 지연이 모두 작습니다.
- 붉은 점 `{KNEE_CANDIDATE_RATIO}`은 무릎점 후보를 표시한 것이며, 해당 가중치를 채택해야 한다는 결론이 아닙니다.

## 무릎점 후보

- 후보 가중치: `{KNEE_CANDIDATE_RATIO}`
- theta: `{knee['theta']}`
- delay_A: `{knee['D_E_sec']:.2f}` s
- delay_N: `{knee['D_G_sec']:.2f}` s

## 운영상 가정

- 가중치 외 조건, 예를 들어 사고 위치·수요·도로망·신호 조건은 모두 동일하게 둡니다.
- 해 탐색은 SPC 기반으로 변동이 잦아드는 시점에서 중단합니다.
- 본 결과는 정책 결정자에게 선택지를 제시하기 위한 민감도 분석입니다.
"""
    (OUTPUT_DIR / "weight_sensitivity_presentation_notes.md").write_text(text, encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    configure_style()
    df = load_data()
    table = write_decision_table(df)
    plot_pareto(df)
    plot_polished_pareto(df)
    plot_must_see_source(df)
    plot_decision_table(table)
    write_talk_track(df)
    print(OUTPUT_DIR)
    print(table.to_string(index=False))
    print(f"\nanalysis_source={INPUT_MD}")


if __name__ == "__main__":
    main()
