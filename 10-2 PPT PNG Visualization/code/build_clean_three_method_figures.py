#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUTS = {
    "BO": PROJECT_ROOT / "gcpcsv/final result/clean/BO.csv",
    "CMA-ES": PROJECT_ROOT / "gcpcsv/final result/clean/CMA.csv",
    "Random Search": PROJECT_ROOT / "gcpcsv/final result/clean/RANDOM.csv",
}
OUTPUT_DIR = PROJECT_ROOT / "results/figures/clean_method_comparison"

COLORS = {
    "BO": "#2F80C5",
    "CMA-ES": "#2A9D78",
    "Random Search": "#C96B42",
}
METHOD_ORDER = ["BO", "CMA-ES", "Random Search"]
THETA_COLUMNS = [
    ("t_lead", "t_lead"),
    ("delta_T_thr", "delta_T_thr"),
    ("G_ext", "G_ext"),
    ("Q_ratio", "Q_ratio"),
    ("tau", "tau"),
]
NAVY = "#0B1F3A"
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


def load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in [
        "global_eval_index",
        "random_eval_index",
        "round",
        "round_theta_index",
        "score",
        "penalty",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def valid_mask(df: pd.DataFrame) -> pd.Series:
    return (
        df["final_status"].astype(str).str.upper().eq("PASS")
        & df["penalty"].fillna(0).eq(0)
        & df["score"].notna()
        & df["score"].lt(10000)
    )


def bo_rounds(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = df.copy()
    raw["presentation_round"] = raw["round"].astype(int)
    raw = raw[raw["presentation_round"].between(1, 50)].copy()
    valid = raw[valid_mask(raw)].copy()
    valid["method"] = "BO"
    valid["round_eval_index"] = valid["round_theta_index"]
    rounds = round_best_with_budget(valid, "BO", 50)
    return valid, rounds


def random_rounds(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = df.copy()
    raw = raw[raw["random_eval_index"].notna()].sort_values("random_eval_index", kind="mergesort").copy()
    raw["presentation_round"] = ((raw["random_eval_index"].astype(int) - 1) // 6 + 1).astype(int)
    raw["round_eval_index"] = ((raw["random_eval_index"].astype(int) - 1) % 6 + 1).astype(int)
    raw = raw[raw["presentation_round"].between(1, 50)].copy()
    valid = raw[valid_mask(raw)].copy()
    valid["method"] = "Random Search"
    rounds = round_best_with_budget(valid, "Random Search", 50)
    return valid, rounds


def cma_rounds(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = df.copy()
    raw = raw[raw["global_eval_index"].notna()].sort_values("global_eval_index", kind="mergesort").copy()
    valid = raw[valid_mask(raw)].copy()
    valid = valid.head(50).copy()
    valid["method"] = "CMA-ES"
    valid["presentation_round"] = np.arange(1, len(valid) + 1)
    valid["round_eval_index"] = 1
    rounds = round_best_with_budget(valid, "CMA-ES", 50)
    return valid, rounds


def round_best_with_budget(valid: pd.DataFrame, method: str, max_round: int) -> pd.DataFrame:
    grouped = (
        valid.groupby("presentation_round", sort=True)
        .agg(
            round_best_score=("score", "min"),
            pass_theta_count=("score", "size"),
        )
        .reset_index()
    )
    rounds = pd.DataFrame({"presentation_round": np.arange(1, max_round + 1)})
    out = rounds.merge(grouped, on="presentation_round", how="left")
    out["method"] = method
    out["best_so_far_score"] = out["round_best_score"].cummin().ffill()
    out["has_pass"] = out["round_best_score"].notna()
    return out


def build_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    bo_valid, bo_round = bo_rounds(load_csv(INPUTS["BO"]))
    cma_valid, cma_round = cma_rounds(load_csv(INPUTS["CMA-ES"]))
    rs_valid, rs_round = random_rounds(load_csv(INPUTS["Random Search"]))
    valid = pd.concat([bo_valid, cma_valid, rs_valid], ignore_index=True)
    rounds = pd.concat([bo_round, cma_round, rs_round], ignore_index=True)
    return valid, rounds


def round_best_theta_table(valid: pd.DataFrame) -> pd.DataFrame:
    sort_cols = ["method", "presentation_round", "score", "round_eval_index"]
    winners = (
        valid.sort_values(sort_cols, kind="mergesort")
        .groupby(["method", "presentation_round"], as_index=False)
        .head(1)
        .copy()
    )
    keep_cols = [
        "method",
        "presentation_round",
        "round_eval_index",
        "score",
        "D_E_sec",
        "D_G_sec",
        *[col for col, _ in THETA_COLUMNS],
    ]
    return winners[keep_cols].sort_values(["method", "presentation_round"], kind="mergesort")


def plot_convergence(rounds: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11.8, 6.4), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    for method in ["BO", "CMA-ES", "Random Search"]:
        curve = rounds[rounds["method"] == method].sort_values("presentation_round")
        x = curve["presentation_round"].to_numpy(dtype=float)
        y = curve["best_so_far_score"].to_numpy(dtype=float)
        ax.step(x, y, where="post", lw=3.0, color=COLORS[method], label=method)
        last = curve[curve["best_so_far_score"].notna()].iloc[-1]
        ax.scatter(
            [last["presentation_round"]],
            [last["best_so_far_score"]],
            s=42,
            color=COLORS[method],
            edgecolor="white",
            linewidth=1.0,
            zorder=5,
        )
        label_y_offset = {"BO": -3.2, "CMA-ES": 0.0, "Random Search": 4.4}[method]
        ax.text(
            float(last["presentation_round"]) + 0.75,
            float(last["best_so_far_score"]) + label_y_offset,
            f"{last['best_so_far_score']:.2f}",
            color=COLORS[method],
            fontsize=9.5,
            fontweight="bold",
            va="center",
        )

    yvals = rounds["best_so_far_score"].dropna().to_numpy(dtype=float)
    ax.set_ylim(max(0, np.nanmin(yvals) - 18), np.nanmax(yvals) + 24)
    ax.set_xlim(1, 52.8)
    ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    ax.set_title("Optimization Convergence by Method", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "FAIL/penalty 제외 · BO=원본 round, RS=평가순서 6개=1라운드, CMA-ES=정상 θ 1개=1라운드 · y축은 누적 최저 Score(best-so-far)",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.4,
    )
    ax.set_xlabel("라운드(방법별 발표 기준)")
    ax.set_ylabel("Best-so-far Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)
    fig.savefig(OUTPUT_DIR / "clean_methods_convergence_fail_excluded.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "clean_methods_convergence_fail_excluded.svg", bbox_inches="tight")
    plt.close(fig)


def plot_round_best(rounds: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11.8, 6.4), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    for method in ["BO", "Random Search", "CMA-ES"]:
        curve = rounds[rounds["method"] == method].sort_values("presentation_round").copy()
        y = curve["round_best_score"].to_numpy(dtype=float)
        x = curve["presentation_round"].to_numpy(dtype=float)
        ax.plot(
            x,
            y,
            color=COLORS[method],
            lw=2.1,
            marker="o",
            markersize=4.6,
            alpha=0.94,
            label=method,
        )

    yvals = rounds["round_best_score"].dropna().to_numpy(dtype=float)
    ax.set_ylim(max(0, np.nanpercentile(yvals, 2) - 22), np.nanmax(yvals) + 32)
    ax.set_xlim(1, 50.8)
    ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    ax.set_title("라운드별 1등 θ의 Score", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "FAIL/penalty 제외 · BO/RS는 각 라운드 정상 후보 중 최저 Score, CMA-ES는 정상 θ 1개당 1라운드 · Score 낮을수록 우수",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.4,
    )
    ax.set_xlabel("라운드")
    ax.set_ylabel("Round-best Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)
    fig.savefig(OUTPUT_DIR / "clean_methods_round_best_fail_excluded.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "clean_methods_round_best_fail_excluded.svg", bbox_inches="tight")
    plt.close(fig)


def plot_round_best_theta(theta_winners: pd.DataFrame) -> None:
    end_label_offsets = {
        ("CMA-ES", "tau"): 0.018,
        ("BO", "tau"): -0.004,
        ("Random Search", "tau"): -0.020,
    }
    fig, axes = plt.subplots(
        len(THETA_COLUMNS),
        1,
        figsize=(12.0, 9.2),
        sharex=True,
        constrained_layout=True,
    )
    fig.patch.set_facecolor("#FBFCFD")

    for ax, (col, label) in zip(axes, THETA_COLUMNS):
        ax.set_facecolor("#FBFCFD")
        for method in METHOD_ORDER:
            curve = theta_winners[theta_winners["method"] == method].sort_values("presentation_round")
            if curve.empty:
                continue
            x = curve["presentation_round"].to_numpy(dtype=float)
            y = curve[col].to_numpy(dtype=float)
            ax.plot(
                x,
                y,
                color=COLORS[method],
                lw=2.0,
                marker="o",
                markersize=3.6,
                markeredgewidth=0.0,
                alpha=0.9,
                label=method,
            )
            last_valid = curve[curve[col].notna()]
            if not last_valid.empty:
                last = last_valid.iloc[-1]
                ax.text(
                    float(last["presentation_round"]) + 0.55,
                    float(last[col]) + end_label_offsets.get((method, col), 0.0),
                    f"{float(last[col]):.2f}",
                    color=COLORS[method],
                    fontsize=8.5,
                    fontweight="bold",
                    va="center",
                )

        yvals = theta_winners[col].dropna().to_numpy(dtype=float)
        if len(yvals):
            ymin, ymax = np.nanmin(yvals), np.nanmax(yvals)
            pad = max((ymax - ymin) * 0.10, 0.04 if col in {"Q_ratio", "tau"} else 1.0)
            ax.set_ylim(ymin - pad, ymax + pad)
        ax.set_ylabel(label, fontsize=10.5)
        ax.grid(True, axis="y", color=GRID, linewidth=0.85)
        ax.grid(False, axis="x")
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["left", "bottom"]].set_color("#D3DAE2")

    axes[0].set_title("Round-best Theta Trajectories", loc="left", fontsize=18, fontweight="bold", pad=20)
    axes[0].text(
        0,
        1.12,
        "Each point is the lowest-score valid candidate in that method's presentation round.",
        transform=axes[0].transAxes,
        color="#627286",
        fontsize=10.4,
    )
    axes[0].legend(
        loc="upper right",
        ncol=3,
        frameon=True,
        facecolor="white",
        edgecolor="#E1E7EF",
        framealpha=0.96,
    )
    axes[-1].set_xlabel("Round")
    axes[-1].set_xlim(1, 52.8)
    axes[-1].xaxis.set_major_locator(MaxNLocator(8, integer=True))
    fig.savefig(OUTPUT_DIR / "clean_methods_round_best_theta_trajectories.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "clean_methods_round_best_theta_trajectories.svg", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    configure_style()
    valid, rounds = build_data()
    theta_winners = round_best_theta_table(valid)
    valid.to_csv(OUTPUT_DIR / "clean_methods_pass_only_points.csv", index=False)
    rounds.to_csv(OUTPUT_DIR / "clean_methods_round_summary_fail_excluded.csv", index=False)
    theta_winners.to_csv(OUTPUT_DIR / "clean_methods_round_best_theta.csv", index=False)
    plot_convergence(rounds)
    plot_round_best(rounds)
    plot_round_best_theta(theta_winners)


if __name__ == "__main__":
    main()
