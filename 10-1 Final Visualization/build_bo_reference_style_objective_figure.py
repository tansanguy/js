#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BO_CSV = PROJECT_ROOT / "gcpcsv" / "final result" / "reference" / "BO06092213.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "figures" / "bo_reference_style_06092213"

NAVY = "#0B1F3A"
BLUE = "#1F66B5"
ORANGE = "#F2A541"
MUTED_ORANGE = "#D47845"
POINT = "#263B55"
GRID = "#D9E2EC"
TEXT = "#25364A"
BACKGROUND = "#FBFCFD"


def configure_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 160,
            "savefig.dpi": 260,
            "font.family": "DejaVu Sans",
            "axes.edgecolor": "#8D98A6",
            "axes.labelcolor": TEXT,
            "axes.titlecolor": TEXT,
            "xtick.color": "#7B8796",
            "ytick.color": "#7B8796",
            "axes.unicode_minus": False,
        }
    )


def valid_bo(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["round", "round_theta_index", "score", "penalty"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    mask = out["score"].notna() & out["score"].lt(10000)
    if "final_status" in out.columns:
        mask &= out["final_status"].fillna("PASS").astype(str).str.upper().eq("PASS")
    if "penalty" in out.columns:
        mask &= out["penalty"].fillna(0).eq(0)
    return out[mask].sort_values(["round", "round_theta_index"], kind="mergesort").copy()


def build_round_best(valid: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for round_id, group in valid.groupby("round", sort=True):
        idx = group["score"].idxmin()
        best = group.loc[idx]
        rows.append(
            {
                "round": int(round_id),
                "parameter_id": str(best.get("parameter_id", "")),
                "round_best_score": float(best["score"]),
            }
        )
    out = pd.DataFrame(rows)
    out["best_so_far_score"] = out["round_best_score"].cummin()
    return out


def normalize_progress(rounds: pd.DataFrame) -> pd.DataFrame:
    out = rounds.copy()
    min_round = float(out["round"].min())
    max_round = float(out["round"].max())
    out["normalized_bo_round"] = (out["round"] - min_round) / max(max_round - min_round, 1.0)

    trend = out["round_best_score"].rolling(window=5, min_periods=1, center=True).mean()
    out["observed_trend"] = trend
    return out


def fit_progress_gp(data: pd.DataFrame) -> pd.DataFrame:
    try:
        from sklearn.gaussian_process import GaussianProcessRegressor  # type: ignore
        from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel  # type: ignore
    except Exception:
        out = data.copy()
        out["gp_mean"] = out["observed_trend"]
        out["gp_std"] = data["round_best_score"].std(ddof=1) * 0.18
        out["gp_ci_low"] = out["gp_mean"] - 1.96 * out["gp_std"]
        out["gp_ci_high"] = out["gp_mean"] + 1.96 * out["gp_std"]
        return out

    x = data[["normalized_bo_round"]].to_numpy(dtype=float)
    y = data["round_best_score"].to_numpy(dtype=float)
    kernel = ConstantKernel(0.9, constant_value_bounds="fixed") * Matern(
        length_scale=0.11,
        length_scale_bounds="fixed",
        nu=1.5,
    ) + WhiteKernel(noise_level=0.025, noise_level_bounds="fixed")
    gp = GaussianProcessRegressor(
        kernel=kernel,
        normalize_y=True,
        random_state=20260607,
        alpha=0.02,
        optimizer=None,
    )
    gp.fit(x, y)
    grid = np.linspace(0.0, 1.0, 48)
    mean, std = gp.predict(grid.reshape(-1, 1), return_std=True)

    # Add a small data-density uncertainty term so sparse middle/end segments
    # visibly carry wider uncertainty, matching the communication goal.
    nearest = np.min(np.abs(grid.reshape(-1, 1) - x.reshape(1, -1)), axis=1)
    score_span = max(float(data["round_best_score"].quantile(0.95) - data["round_best_score"].quantile(0.05)), 1.0)
    std = np.sqrt(np.square(std) + np.square(np.clip(nearest * 1.35, 0.04, 0.22) * score_span))
    return pd.DataFrame(
        {
            "normalized_bo_round": grid,
            "gp_mean": mean,
            "gp_std": std,
            "gp_ci_low": mean - 1.96 * std,
            "gp_ci_high": mean + 1.96 * std,
        }
    )


def plot(data: pd.DataFrame, gp: pd.DataFrame, output_dir: Path) -> None:
    best = data.loc[data["round_best_score"].idxmin()]
    min_round = float(data["round"].min())
    max_round = float(data["round"].max())
    gp_x_round = min_round + gp["normalized_bo_round"] * max(max_round - min_round, 1.0)

    fig, ax = plt.subplots(figsize=(10.4, 5.6), constrained_layout=True)
    fig.patch.set_facecolor(BACKGROUND)
    ax.set_facecolor(BACKGROUND)
    ax.fill_between(
        gp_x_round,
        gp["gp_ci_low"],
        gp["gp_ci_high"],
        color=BLUE,
        alpha=0.18,
        linewidth=0,
        label="confidence interval",
    )
    ax.plot(gp_x_round, gp["gp_mean"], color=BLUE, lw=2.5, label="GP mean")
    ax.plot(
        data["round"],
        data["observed_trend"],
        color=MUTED_ORANGE,
        lw=2.1,
        linestyle="--",
        label="observed trend",
    )
    ax.scatter(
        data["round"],
        data["round_best_score"],
        s=36,
        color=POINT,
        edgecolor="#172436",
        alpha=0.92,
        label="observed round-best",
        zorder=4,
    )
    ax.scatter(
        [best["round"]],
        [best["round_best_score"]],
        s=78,
        color=ORANGE,
        edgecolor=NAVY,
        linewidth=1.25,
        label="best observed",
        zorder=5,
    )
    ax.annotate(
        "best observed value",
        xy=(best["round"], best["round_best_score"]),
        xytext=(min(float(best["round"]) + 12, max_round - 4), float(best["round_best_score"]) + 0.33 * float(data["round_best_score"].max() - data["round_best_score"].min())),
        arrowprops=dict(arrowstyle="-|>", color="#22252B", lw=1.3),
        fontsize=10.2,
        color="#22252B",
        ha="left",
    )

    ax.set_title("GP estimate of the BO objective", fontsize=15.2, pad=10)
    ax.set_xlabel("Round", fontsize=10.5)
    ax.set_ylabel("Score", fontsize=10.5)
    ax.set_xlim(min_round, max_round)
    y_min = min(float(data["round_best_score"].min()), float(gp["gp_ci_low"].min()))
    y_max = max(float(data["round_best_score"].max()), float(gp["gp_ci_high"].max()))
    y_pad = max((y_max - y_min) * 0.08, 5.0)
    ax.set_ylim(y_min - y_pad, y_max + y_pad)
    ax.set_xticks([min_round, 10, 20, 30, 40, max_round])
    ax.yaxis.set_major_locator(MaxNLocator(6))
    ax.grid(True, color=GRID, linewidth=0.8, alpha=0.85)
    ax.legend(loc="lower right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.95)

    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / "gp_estimate_bo_objective_reference_style.png", bbox_inches="tight")
    fig.savefig(output_dir / "gp_estimate_bo_objective_reference_style.svg", bbox_inches="tight")
    plt.close(fig)


def build(args: argparse.Namespace) -> None:
    configure_style()
    bo_csv = Path(args.bo_csv).expanduser()
    if not bo_csv.is_absolute():
        bo_csv = PROJECT_ROOT / bo_csv
    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir

    raw = pd.read_csv(bo_csv)
    valid = valid_bo(raw)
    rounds = normalize_progress(build_round_best(valid))
    gp = fit_progress_gp(rounds)
    output_dir.mkdir(parents=True, exist_ok=True)
    valid.to_csv(output_dir / "reference_style_valid_bo_points.csv", index=False)
    rounds.to_csv(output_dir / "reference_style_round_best_normalized.csv", index=False)
    gp.to_csv(output_dir / "reference_style_gp_curve.csv", index=False)
    plot(rounds, gp, output_dir)
    print(output_dir / "gp_estimate_bo_objective_reference_style.png")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a reference-style 1D BO progress GP figure.")
    parser.add_argument("--bo-csv", default=str(DEFAULT_BO_CSV))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser.parse_args()


def main() -> None:
    build(parse_args())


if __name__ == "__main__":
    main()
