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
GRID = "#D9E2EC"
TEXT = "#25364A"
ORANGE = "#F2A541"
MUTED_ORANGE = "#D47845"


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
            "savefig.dpi": 260,
            "axes.unicode_minus": False,
            "axes.edgecolor": "#C7D1DC",
            "axes.labelcolor": TEXT,
            "xtick.color": "#6B7788",
            "ytick.color": "#6B7788",
            "axes.titlecolor": NAVY,
        }
    )


def clean_bo() -> pd.DataFrame:
    df = pd.read_csv(INPUT)
    numeric_cols = [
        "global_eval_index",
        "round",
        "round_theta_index",
        "score",
        "penalty",
        "surrogate_mean",
        "surrogate_ci_low",
        "surrogate_ci_high",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "global_eval_index" in df.columns:
        df = df.sort_values(["global_eval_index", "round", "round_theta_index"], kind="mergesort").copy()
        df["eval_index"] = df["global_eval_index"]
    else:
        df = df.sort_values(["round", "round_theta_index"], kind="mergesort").copy()
        df["eval_index"] = np.arange(1, len(df) + 1)

    status = df["final_status"].astype(str).str.upper() if "final_status" in df.columns else "PASS"
    no_penalty = df["penalty"].fillna(0).eq(0) if "penalty" in df.columns else True
    valid = status.eq("PASS") & no_penalty & df["score"].notna() & df["score"].lt(10000)
    cleaned = df.loc[valid].copy()
    cleaned["plot_index"] = np.arange(1, len(cleaned) + 1)
    return cleaned


def plot() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bo = clean_bo()
    bo.to_csv(OUTPUT_DIR / "figure2_bo_surrogate_trace_clean_used_pass_only.csv", index=False)

    surrogate = bo.dropna(subset=["surrogate_mean", "surrogate_ci_low", "surrogate_ci_high"]).copy()
    best_row = bo.loc[bo["score"].idxmin()]

    fig, ax = plt.subplots(figsize=(11.2, 6.2), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    x = surrogate["plot_index"].to_numpy(dtype=float)
    mean = surrogate["surrogate_mean"].to_numpy(dtype=float)
    low = surrogate["surrogate_ci_low"].to_numpy(dtype=float)
    high = surrogate["surrogate_ci_high"].to_numpy(dtype=float)
    order = np.argsort(x)
    x, mean, low, high = x[order], mean[order], low[order], high[order]

    y_reference = np.concatenate([bo["score"].to_numpy(dtype=float), mean, low, high])
    y_reference = y_reference[np.isfinite(y_reference)]
    lower = max(0, np.percentile(y_reference, 3) - 26)
    upper = np.percentile(y_reference, 95) + 34

    ax.fill_between(x, low, high, color=BLUE, alpha=0.14, linewidth=0, label="surrogate CI")
    ax.plot(x, mean, color=BLUE, lw=2.35, alpha=0.96, label="surrogate mean")
    ax.scatter(
        bo["plot_index"],
        bo["score"],
        s=18,
        color=NAVY,
        alpha=0.74,
        label="observed score",
        zorder=4,
    )
    ax.scatter(
        [best_row["plot_index"]],
        [best_row["score"]],
        s=82,
        color=ORANGE,
        edgecolor=NAVY,
        linewidth=1.25,
        zorder=6,
        label="best observed",
    )

    text_x = min(float(best_row["plot_index"]) + 12, float(bo["plot_index"].max()) - 28)
    text_y = min(float(best_row["score"]) + 45, upper - 20)
    ax.annotate(
        f"best observed\n{best_row['score']:.2f}",
        xy=(best_row["plot_index"], best_row["score"]),
        xytext=(text_x, text_y),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.15),
        fontsize=9.7,
        color=NAVY,
        ha="left",
        va="center",
    )

    ax.set_ylim(lower, upper)
    ax.set_xlim(1, int(bo["plot_index"].max()) + 4)
    ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    ax.set_title("BO surrogate 추정과 관측값", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "x축은 FAIL/penalty를 제외한 BO 정상 평가(PASS) θ 후보 순서",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.5,
    )
    ax.set_xlabel("BO 정상 평가 순서(PASS θ 후보)")
    ax.set_ylabel("Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)

    fig.savefig(OUTPUT_DIR / "figure2_bo_surrogate_trace_clean.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure2_bo_surrogate_trace_clean.svg", bbox_inches="tight")
    plt.close(fig)


def round_summary(bo: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for round_id, group in bo.groupby("round", sort=True):
        surrogate = group.dropna(subset=["surrogate_mean", "surrogate_ci_low", "surrogate_ci_high"])
        best_idx = group["score"].idxmin()
        best = group.loc[best_idx]
        rows.append(
            {
                "round": int(round_id),
                "pass_theta_count": int(len(group)),
                "round_best_score": float(best["score"]),
                "round_best_parameter_id": best.get("parameter_id", ""),
                "round_best_global_eval_index": int(best["eval_index"]),
                "surrogate_mean": float(surrogate["surrogate_mean"].mean()) if len(surrogate) else np.nan,
                "surrogate_ci_low": float(surrogate["surrogate_ci_low"].mean()) if len(surrogate) else np.nan,
                "surrogate_ci_high": float(surrogate["surrogate_ci_high"].mean()) if len(surrogate) else np.nan,
            }
        )
    summary = pd.DataFrame(rows)
    summary["best_so_far_score"] = summary["round_best_score"].cummin()
    return summary


def plot_by_round() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bo = clean_bo()
    summary = round_summary(bo)
    summary.to_csv(OUTPUT_DIR / "figure2_bo_surrogate_trace_clean_by_round.csv", index=False)

    surrogate = summary.dropna(subset=["surrogate_mean", "surrogate_ci_low", "surrogate_ci_high"]).copy()
    best_row = summary.loc[summary["round_best_score"].idxmin()]

    fig, ax = plt.subplots(figsize=(11.2, 6.2), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    x = surrogate["round"].to_numpy(dtype=float)
    mean = surrogate["surrogate_mean"].to_numpy(dtype=float)
    low = surrogate["surrogate_ci_low"].to_numpy(dtype=float)
    high = surrogate["surrogate_ci_high"].to_numpy(dtype=float)

    y_reference = np.concatenate([summary["round_best_score"].to_numpy(dtype=float), mean, low, high])
    y_reference = y_reference[np.isfinite(y_reference)]
    lower = max(0, np.percentile(y_reference, 3) - 24)
    upper = np.percentile(y_reference, 95) + 34

    gap_breaks = np.where(np.diff(x) > 1)[0] + 1
    segments = np.split(np.arange(len(x)), gap_breaks)
    ci_label_used = False
    mean_label_used = False
    for segment in segments:
        if len(segment) < 2:
            continue
        ci_label = None if ci_label_used else "surrogate CI"
        mean_label = None if mean_label_used else "surrogate mean"
        ax.fill_between(
            x[segment],
            low[segment],
            high[segment],
            color=BLUE,
            alpha=0.14,
            linewidth=0,
            label=ci_label,
        )
        ax.plot(
            x[segment],
            mean[segment],
            color=BLUE,
            lw=2.35,
            alpha=0.96,
            label=mean_label,
        )
        ci_label_used = True
        mean_label_used = True

    ax.scatter(
        summary["round"],
        summary["round_best_score"],
        s=28,
        color=NAVY,
        alpha=0.76,
        label="round-best observed",
        zorder=4,
    )
    ax.scatter(
        [best_row["round"]],
        [best_row["round_best_score"]],
        s=88,
        color=ORANGE,
        edgecolor=NAVY,
        linewidth=1.25,
        zorder=6,
        label="best observed",
    )

    text_x = min(float(best_row["round"]) + 4.5, 42)
    text_y = min(float(best_row["round_best_score"]) + 43, upper - 18)
    ax.annotate(
        f"best observed\nR{int(best_row['round'])} · {best_row['round_best_score']:.2f}",
        xy=(best_row["round"], best_row["round_best_score"]),
        xytext=(text_x, text_y),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.15),
        fontsize=9.7,
        color=NAVY,
        ha="left",
        va="center",
    )

    ax.set_ylim(lower, upper)
    ax.set_xlim(1, 50)
    ax.xaxis.set_major_locator(MaxNLocator(9, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    ax.set_title("BO surrogate 추정과 관측값", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "x축은 BO 라운드 · 관측값은 각 라운드 PASS 후보 중 최저 Score · FAIL/penalty-only 라운드는 제외",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.5,
    )
    ax.set_xlabel("BO 라운드")
    ax.set_ylabel("Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)

    fig.savefig(OUTPUT_DIR / "figure2_bo_surrogate_trace_clean_by_round.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure2_bo_surrogate_trace_clean_by_round.svg", bbox_inches="tight")
    plt.close(fig)


def moving_average(values: np.ndarray, window: int = 5) -> np.ndarray:
    if len(values) < 3:
        return values
    window = min(window, len(values) if len(values) % 2 == 1 else len(values) - 1)
    if window < 3:
        return values
    pad = window // 2
    padded = np.pad(values, (pad, pad), mode="edge")
    kernel = np.ones(window) / window
    return np.convolve(padded, kernel, mode="valid")


def normalize_as_value(values: pd.Series | np.ndarray, score_min: float, score_max: float) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    span = max(score_max - score_min, 1e-9)
    return np.clip((score_max - arr) / span, 0, 1)


def plot_gp_estimate_by_round() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bo = clean_bo()
    summary = round_summary(bo)
    surrogate = summary.dropna(subset=["surrogate_mean", "surrogate_ci_low", "surrogate_ci_high"]).copy()

    score_min = float(
        np.nanmin(
            np.concatenate(
                [
                    summary["round_best_score"].to_numpy(dtype=float),
                    surrogate["surrogate_mean"].to_numpy(dtype=float),
                    surrogate["surrogate_ci_low"].to_numpy(dtype=float),
                    surrogate["surrogate_ci_high"].to_numpy(dtype=float),
                ]
            )
        )
    )
    score_max = float(
        np.nanmax(
            np.concatenate(
                [
                    summary["round_best_score"].to_numpy(dtype=float),
                    surrogate["surrogate_mean"].to_numpy(dtype=float),
                    surrogate["surrogate_ci_low"].to_numpy(dtype=float),
                    surrogate["surrogate_ci_high"].to_numpy(dtype=float),
                ]
            )
        )
    )

    summary["x_norm"] = (summary["round"] - 1) / 49
    surrogate["x_norm"] = (surrogate["round"] - 1) / 49
    summary["observed_value"] = normalize_as_value(summary["round_best_score"], score_min, score_max)
    surrogate["gp_mean_value"] = normalize_as_value(surrogate["surrogate_mean"], score_min, score_max)
    surrogate["gp_ci_low_value"] = normalize_as_value(surrogate["surrogate_ci_high"], score_min, score_max)
    surrogate["gp_ci_high_value"] = normalize_as_value(surrogate["surrogate_ci_low"], score_min, score_max)

    best_row = summary.loc[summary["round_best_score"].idxmin()]
    best_value = float(normalize_as_value([best_row["round_best_score"]], score_min, score_max)[0])

    fig, ax = plt.subplots(figsize=(9.8, 5.2), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    x = surrogate["round"].to_numpy(dtype=float)
    mean = surrogate["gp_mean_value"].to_numpy(dtype=float)
    low = surrogate["gp_ci_low_value"].to_numpy(dtype=float)
    high = surrogate["gp_ci_high_value"].to_numpy(dtype=float)
    order = np.argsort(x)
    x, mean, low, high = x[order], mean[order], low[order], high[order]

    observed_x = summary["round"].to_numpy(dtype=float)
    observed_y = summary["observed_value"].to_numpy(dtype=float)
    trend = moving_average(observed_y, window=5)

    ax.fill_between(x, low, high, color=BLUE, alpha=0.22, linewidth=0, label="confidence interval")
    ax.plot(x, mean, color="#0F5CB8", lw=2.5, label="GP mean")
    ax.plot(observed_x, trend, color=MUTED_ORANGE, lw=2.1, ls="--", label="observed trend")
    ax.scatter(observed_x, observed_y, s=31, color=NAVY, alpha=0.82, label="observed round-best", zorder=4)
    ax.scatter(
        [float(best_row["round"])],
        [best_value],
        s=72,
        color=ORANGE,
        edgecolor=NAVY,
        linewidth=1.15,
        zorder=6,
        label="best observed",
    )
    ax.annotate(
        "best observed value",
        xy=(float(best_row["round"]), best_value),
        xytext=(min(float(best_row["round"]) + 12, float(summary["round"].max()) - 4), min(best_value + 0.28, 0.94)),
        arrowprops=dict(arrowstyle="-|>", color="#111111", lw=1.45),
        fontsize=11.5,
        color="#222222",
        ha="left",
        va="center",
    )

    ax.set_xlim(float(summary["round"].min()), float(summary["round"].max()))
    ax.set_ylim(0, 1)
    ax.xaxis.set_major_locator(MaxNLocator(6, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(6))
    ax.grid(True, color="#E3E8EF", linewidth=0.85)
    ax.spines[["top", "right"]].set_visible(True)
    for spine in ax.spines.values():
        spine.set_color("#7A7F85")
        spine.set_linewidth(1.0)
    ax.set_title("GP estimate of the BO objective", fontsize=15.5, color="#1E2732", pad=8)
    ax.set_xlabel("BO Round")
    ax.set_ylabel("normalized value\n(lower Score = higher value)")
    ax.legend(loc="lower right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)

    fig.savefig(OUTPUT_DIR / "figure2b_bo_gp_estimate_by_round.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure2b_bo_gp_estimate_by_round.svg", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    configure_style()
    plot()
    plot_by_round()
    plot_gp_estimate_by_round()


if __name__ == "__main__":
    main()
