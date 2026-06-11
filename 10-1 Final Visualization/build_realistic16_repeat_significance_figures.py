#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator
from scipy import stats


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_CSV = (
    PROJECT_ROOT
    / "gcpcsv"
    / "final result"
    / "clean"
    / "New_Learn"
    / "Realistic16_Final_Results"
    / "Realistic16_seoul_repeat_rows.csv"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "figures" / "realistic16_repeat_significance"

NAVY = "#0B1F3A"
BLUE = "#2F80C5"
BLUE_LIGHT = "#D9EAF8"
ORANGE = "#F2A541"
GRAY = "#A9B5C3"
GRAY_DARK = "#6B7788"
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


def short_id(parameter_id: str) -> str:
    parts = str(parameter_id).split("_")
    if len(parts) >= 3 and parts[0] == "bo":
        return f"{parts[1]}_{parts[2]}"
    return str(parameter_id)[:12]


def require_columns(df: pd.DataFrame) -> None:
    required = {
        "parameter_id",
        "objective_score",
        "D_E_sec",
        "D_G_sec",
        "final_status",
        "failed",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Missing required columns: {missing}")


def load_valid_rows(input_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(input_csv)
    require_columns(df)
    for col in ["objective_score", "D_E_sec", "D_G_sec", "repeat_id"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["failed_bool"] = df["failed"].astype(str).str.lower().isin({"true", "1", "yes"})
    valid = df[
        df["objective_score"].notna()
        & df["D_E_sec"].notna()
        & df["D_G_sec"].notna()
        & df["final_status"].astype(str).str.upper().eq("PASS")
        & ~df["failed_bool"]
    ].copy()
    if valid.empty:
        raise ValueError("No valid PASS/non-failed rows with objective_score, D_E_sec, and D_G_sec.")
    return valid


def pooled_cohen_d(x: pd.Series, best: pd.Series, diff: float) -> float:
    n_x = len(x)
    n_b = len(best)
    if n_x < 2 or n_b < 2:
        return float("nan")
    pooled_var = ((n_x - 1) * x.var(ddof=1) + (n_b - 1) * best.var(ddof=1)) / (n_x + n_b - 2)
    if pooled_var <= 0 or not np.isfinite(pooled_var):
        return float("nan")
    return float(diff / math.sqrt(pooled_var))


def build_summary(valid: pd.DataFrame, alpha: float) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for parameter_id, group in valid.groupby("parameter_id", sort=False):
        scores = group["objective_score"].astype(float)
        n = int(len(scores))
        std = float(scores.std(ddof=1))
        se = std / math.sqrt(n) if n else float("nan")
        ci95 = float(stats.t.ppf(0.975, n - 1) * se) if n > 1 and np.isfinite(se) else float("nan")
        rows.append(
            {
                "parameter_id": parameter_id,
                "short_id": short_id(parameter_id),
                "n": n,
                "mean_score": float(scores.mean()),
                "std_score": std,
                "ci95": ci95,
                "min_score": float(scores.min()),
                "q25_score": float(scores.quantile(0.25)),
                "median_score": float(scores.median()),
                "q75_score": float(scores.quantile(0.75)),
                "max_score": float(scores.max()),
                "mean_D_E_sec": float(group["D_E_sec"].mean()),
                "mean_D_G_sec": float(group["D_G_sec"].mean()),
                "fail_count": int(group["failed_bool"].sum()),
            }
        )
    summary = pd.DataFrame(rows).sort_values("mean_score", ascending=True, kind="mergesort").reset_index(drop=True)
    summary["rank"] = np.arange(1, len(summary) + 1)

    best_pid = str(summary.loc[0, "parameter_id"])
    best_scores = valid.loc[valid["parameter_id"].eq(best_pid), "objective_score"].astype(float)
    p_values = []
    t_stats = []
    diffs = []
    effects = []
    for _, row in summary.iterrows():
        scores = valid.loc[valid["parameter_id"].eq(row["parameter_id"]), "objective_score"].astype(float)
        diff = float(row["mean_score"] - summary.loc[0, "mean_score"])
        if row["rank"] == 1:
            p_value = float("nan")
            t_stat = float("nan")
            effect = 0.0
        else:
            result = stats.ttest_ind(scores, best_scores, equal_var=False)
            p_value = float(result.pvalue)
            t_stat = float(result.statistic)
            effect = pooled_cohen_d(scores, best_scores, diff)
        p_values.append(p_value)
        t_stats.append(t_stat)
        diffs.append(diff)
        effects.append(effect)
    summary["diff_vs_best"] = diffs
    summary["welch_t_vs_best"] = t_stats
    summary["welch_p_vs_best"] = p_values
    summary["cohen_d_vs_best"] = effects

    first_significant = summary[(summary["rank"] > 1) & (summary["welch_p_vs_best"] < alpha)]
    top_k = int(first_significant.iloc[0]["rank"] - 1) if not first_significant.empty else int(summary["rank"].max())
    summary["accepted_top3"] = summary["rank"].le(top_k)
    summary["accepted_cut"] = summary["rank"].le(top_k)
    summary.attrs["top_k"] = top_k
    summary.attrs["alpha"] = alpha
    return summary


def axis_clean(ax: plt.Axes, grid_axis: str = "x") -> None:
    ax.set_facecolor(BACKGROUND)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    if grid_axis in {"x", "both"}:
        ax.grid(True, axis="x", color=GRID, linewidth=0.85, alpha=0.9)
    if grid_axis in {"y", "both"}:
        ax.grid(True, axis="y", color=GRID, linewidth=0.85, alpha=0.9)


def rank_label(row: pd.Series) -> str:
    return f"R{int(row['rank']):02d}  {row['short_id']}  n={int(row['n'])}"


def plot_repeat_significance_ranking(valid: pd.DataFrame, summary: pd.DataFrame, output_dir: Path) -> None:
    top_k = int(summary.attrs["top_k"])
    rank_map = summary.set_index("parameter_id")["rank"].to_dict()

    fig, ax = plt.subplots(figsize=(13.4, 6.8), constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.82, bottom=0.20)
    axis_clean(ax, "y")

    accepted = summary["accepted_cut"].to_numpy()
    x = summary["rank"].to_numpy(dtype=float)
    colors = np.where(accepted, BLUE, GRAY)
    ax.axvspan(0.5, top_k + 0.5, color=BLUE_LIGHT, alpha=0.42, zorder=0)
    ax.plot(summary["rank"], summary["mean_score"], color="#91A3B7", linewidth=1.1, alpha=0.65, zorder=2)
    for _, row in summary.iterrows():
        ax.errorbar(
            row["rank"],
            row["mean_score"],
            yerr=row["ci95"],
            fmt="none",
            ecolor=BLUE if row["accepted_cut"] else GRAY_DARK,
            elinewidth=2.0,
            capsize=4,
            alpha=0.90,
            zorder=3,
        )
    ax.scatter(x, summary["mean_score"], s=70, color=colors, edgecolor="white", linewidth=1.0, zorder=4)
    ax.scatter(1, summary.loc[0, "mean_score"], s=138, color=ORANGE, edgecolor=NAVY, linewidth=1.2, zorder=5)
    ax.axhline(summary.loc[0, "mean_score"], color=ORANGE, linestyle=":", linewidth=1.1, alpha=0.72, zorder=1)

    for _, row in summary.iterrows():
        if row["rank"] <= 4:
            ax.text(row["rank"], row["mean_score"] + row["ci95"] + 4.2, f"{row['mean_score']:.1f}", va="bottom", ha="center", fontsize=8.7, color=NAVY)

    ax.axvline(top_k + 0.5, color=NAVY, linestyle="--", linewidth=1.2, alpha=0.86)
    rank4 = summary.loc[summary["rank"].eq(top_k + 1)].iloc[0] if top_k < len(summary) else None
    if rank4 is not None:
        ci_low = float((summary["mean_score"] - summary["ci95"]).min())
        ci_high = float((summary["mean_score"] + summary["ci95"]).max())
        focused_lower = max(0.0, ci_low - 8.0)
        focused_upper = ci_high + 12.0
        ax.annotate(
            f"Top {top_k} Cut\nRank {top_k + 1} vs best: p={rank4['welch_p_vs_best']:.3f}",
            xy=(top_k + 0.5, rank4["mean_score"]),
            xytext=(top_k + 1.0, focused_upper - 7.0),
            arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.1),
            color=NAVY,
            fontsize=9.0,
            ha="left",
            va="center",
        )

    labels = [f"R{int(row['rank']):02d}\n{row['short_id']}\nn={int(row['n'])}" for _, row in summary.iterrows()]
    ax.set_xticks(summary["rank"])
    ax.set_xticklabels(labels, fontsize=7.5)
    ax.set_xlabel("Candidate Rank", fontsize=10.8)
    ax.set_ylabel("Objective Score", fontsize=10.8)
    ax.set_title("Repeat Simulation Significance", loc="left", fontsize=17.0, fontweight="bold", pad=18)
    ax.text(
        0.0,
        1.015,
        "Focused scale: mean +/- 95% CI over all valid repeat scores; lower score is better.",
        transform=ax.transAxes,
        color=MUTED,
        fontsize=9.6,
    )
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.set_xlim(0.45, len(summary) + 0.55)
    ci_low = float((summary["mean_score"] - summary["ci95"]).min())
    ci_high = float((summary["mean_score"] + summary["ci95"]).max())
    ax.set_ylim(max(0.0, ci_low - 8.0), ci_high + 12.0)

    handles = [
        Patch(facecolor=BLUE_LIGHT, edgecolor="none", alpha=0.55, label=f"Accepted Top {top_k}"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=ORANGE, markeredgecolor=NAVY, markersize=8, label="Best mean score"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=GRAY, markeredgecolor="white", markersize=6, label="Significantly worse group"),
    ]
    ax.legend(handles=handles, loc="lower right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)

    fig.savefig(output_dir / "realistic16_repeat_significance_ranking.png", bbox_inches="tight")
    fig.savefig(output_dir / "realistic16_repeat_significance_ranking.svg", bbox_inches="tight")
    fig.savefig(output_dir / "realistic16_repeat_significance_pivot.png", bbox_inches="tight")
    fig.savefig(output_dir / "realistic16_repeat_significance_pivot.svg", bbox_inches="tight")
    plt.close(fig)


def plot_repeat_score_distribution(valid: pd.DataFrame, summary: pd.DataFrame, output_dir: Path) -> None:
    ordered_ids = summary["parameter_id"].tolist()
    data = [valid.loc[valid["parameter_id"].eq(pid), "objective_score"].to_numpy(dtype=float) for pid in ordered_ids]
    positions = np.arange(1, len(data) + 1)
    top_k = int(summary.attrs["top_k"])

    fig, ax = plt.subplots(figsize=(13.0, 6.8), constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    fig.subplots_adjust(left=0.07, right=0.985, top=0.83, bottom=0.23)
    axis_clean(ax, "y")

    violin = ax.violinplot(data, positions=positions, widths=0.78, showmeans=False, showmedians=False, showextrema=False)
    for idx, body in enumerate(violin["bodies"], start=1):
        body.set_facecolor(BLUE if idx <= top_k else "#CBD3DD")
        body.set_edgecolor("none")
        body.set_alpha(0.34 if idx <= top_k else 0.28)

    box = ax.boxplot(
        data,
        positions=positions,
        widths=0.42,
        patch_artist=True,
        showfliers=False,
        medianprops=dict(color=NAVY, linewidth=1.2),
        whiskerprops=dict(color=GRAY_DARK, linewidth=0.9),
        capprops=dict(color=GRAY_DARK, linewidth=0.9),
    )
    for idx, patch in enumerate(box["boxes"], start=1):
        patch.set_facecolor(BLUE if idx <= top_k else "white")
        patch.set_alpha(0.28 if idx <= top_k else 0.50)
        patch.set_edgecolor(BLUE if idx <= top_k else GRAY)

    rng = np.random.default_rng(20260610)
    for pos, scores in zip(positions, data):
        jitter = rng.uniform(-0.18, 0.18, size=len(scores))
        ax.scatter(pos + jitter, scores, s=7, color="#8A97A6", alpha=0.17, linewidth=0, zorder=2)

    ax.axvspan(0.5, top_k + 0.5, color=BLUE_LIGHT, alpha=0.28, zorder=0)
    ax.axvline(top_k + 0.5, color=NAVY, linestyle="--", linewidth=1.1)
    ax.text(top_k + 0.58, ax.get_ylim()[1] - 4, f"Top {top_k} Cut", color=NAVY, fontsize=9.2, fontweight="bold", va="top")

    labels = [f"R{int(row['rank']):02d}\n{row['short_id']}\nn={int(row['n'])}" for _, row in summary.iterrows()]
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, fontsize=7.7, rotation=0)
    ax.set_xlabel("Candidate Rank", fontsize=10.8)
    ax.set_ylabel("Objective Score", fontsize=10.8)
    ax.set_title("Repeat Score Distribution", loc="left", fontsize=17.0, fontweight="bold", pad=18)
    ax.text(
        0.0,
        1.015,
        "Uneven repeat counts are shown under each candidate; distributions use all valid repeat scores.",
        transform=ax.transAxes,
        color=MUTED,
        fontsize=9.5,
    )
    ax.yaxis.set_major_locator(MaxNLocator(7))

    fig.savefig(output_dir / "realistic16_repeat_score_distribution.png", bbox_inches="tight")
    fig.savefig(output_dir / "realistic16_repeat_score_distribution.svg", bbox_inches="tight")
    plt.close(fig)


def plot_delay_tradeoff(summary: pd.DataFrame, output_dir: Path) -> None:
    top_k = int(summary.attrs["top_k"])
    accepted = summary["accepted_cut"].to_numpy()

    fig, ax = plt.subplots(figsize=(8.8, 6.8), constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    fig.subplots_adjust(left=0.13, right=0.97, top=0.83, bottom=0.13)
    axis_clean(ax, "both")

    score = summary["mean_score"].to_numpy(dtype=float)
    sizes = np.interp(summary["n"], (summary["n"].min(), summary["n"].max()), (55, 160))
    ax.scatter(
        summary["mean_D_G_sec"],
        summary["mean_D_E_sec"],
        s=sizes,
        c=np.where(accepted, BLUE, "#AAB6C4"),
        edgecolor=np.where(accepted, NAVY, "white"),
        linewidth=np.where(accepted, 1.05, 0.65),
        alpha=0.93,
        zorder=3,
    )
    best = summary.iloc[0]
    ax.scatter(best["mean_D_G_sec"], best["mean_D_E_sec"], s=185, color=ORANGE, edgecolor=NAVY, linewidth=1.2, zorder=5)

    for _, row in summary.iterrows():
        if row["rank"] <= top_k + 1:
            ax.text(
                row["mean_D_G_sec"] + 0.45,
                row["mean_D_E_sec"] + 0.35,
                f"R{int(row['rank']):02d}",
                fontsize=9.0,
                color=NAVY,
                fontweight="bold" if row["rank"] <= top_k else "normal",
            )

    ax.set_xlabel("D_G General Delay (s)", fontsize=10.8)
    ax.set_ylabel("D_E Emergency Delay (s)", fontsize=10.8)
    ax.set_title("Delay Trade-off of Accepted Candidates", loc="left", fontsize=16.2, fontweight="bold", pad=18)
    ax.text(
        0.0,
        1.015,
        f"Top {top_k} candidates remain statistically competitive with the best; marker size reflects repeat count.",
        transform=ax.transAxes,
        color=MUTED,
        fontsize=9.3,
    )
    ax.xaxis.set_major_locator(MaxNLocator(6))
    ax.yaxis.set_major_locator(MaxNLocator(6))

    handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor=BLUE, markeredgecolor=NAVY, markersize=8, label=f"Accepted Top {top_k}"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor="#AAB6C4", markeredgecolor="white", markersize=7, label="Rejected by significance cut"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor=ORANGE, markeredgecolor=NAVY, markersize=9, label="Best mean score"),
    ]
    ax.legend(handles=handles, loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)

    fig.savefig(output_dir / "realistic16_delay_tradeoff_top3.png", bbox_inches="tight")
    fig.savefig(output_dir / "realistic16_delay_tradeoff_top3.svg", bbox_inches="tight")
    plt.close(fig)


def validate_outputs(valid: pd.DataFrame, summary: pd.DataFrame, alpha: float) -> None:
    if valid["parameter_id"].nunique() != 16:
        raise AssertionError(f"Expected 16 candidates, got {valid['parameter_id'].nunique()}")
    if not valid["final_status"].astype(str).str.upper().eq("PASS").all():
        raise AssertionError("Non-PASS rows remain in valid data")
    if valid["failed_bool"].any():
        raise AssertionError("Failed rows remain in valid data")
    if "candidate_rank" in valid.columns and valid["candidate_rank"].nunique(dropna=True) == len(summary):
        raise AssertionError("candidate_rank appears to have been used as a candidate key")
    if not summary["mean_score"].is_monotonic_increasing:
        raise AssertionError("Summary is not ranked by mean objective score")
    first_significant = summary[(summary["rank"] > 1) & (summary["welch_p_vs_best"] < alpha)]
    expected_top_k = int(first_significant.iloc[0]["rank"] - 1) if not first_significant.empty else int(summary["rank"].max())
    if int(summary.attrs["top_k"]) != expected_top_k:
        raise AssertionError("Top-k cut does not match first significant Welch p-value")
    counts = valid.groupby("parameter_id").size()
    merged = summary.set_index("parameter_id")["n"]
    if not counts.equals(merged[counts.index]):
        raise AssertionError("Summary repeat counts do not match raw rows")


def build(args: argparse.Namespace) -> None:
    configure_style()
    input_csv = resolve_path(args.input_csv)
    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not input_csv.exists():
        fallback_csv = output_dir / "realistic16_valid_repeat_rows_used.csv"
        if fallback_csv.exists():
            input_csv = fallback_csv
        else:
            raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    valid = load_valid_rows(input_csv)
    summary = build_summary(valid, args.alpha)
    validate_outputs(valid, summary, args.alpha)

    summary_columns = [
        "rank",
        "parameter_id",
        "n",
        "mean_score",
        "std_score",
        "ci95",
        "diff_vs_best",
        "welch_p_vs_best",
        "cohen_d_vs_best",
        "mean_D_E_sec",
        "mean_D_G_sec",
        "accepted_top3",
    ]
    summary[summary_columns].to_csv(output_dir / "realistic16_candidate_repeat_summary.csv", index=False)
    valid.to_csv(output_dir / "realistic16_valid_repeat_rows_used.csv", index=False)
    rank_lookup = summary.set_index("parameter_id")["rank"].to_dict()
    pivot_source = valid.copy()
    pivot_source["rank_label"] = pivot_source["parameter_id"].map(rank_lookup).map(lambda value: f"R{int(value):02d}")
    if "repeat_id" in pivot_source.columns:
        pivot = pivot_source.pivot_table(
            index="repeat_id",
            columns="rank_label",
            values="objective_score",
            aggfunc="mean",
        )
        ordered_columns = [f"R{int(rank):02d}" for rank in summary["rank"]]
        pivot = pivot.reindex(columns=ordered_columns)
        pivot.to_csv(output_dir / "realistic16_repeat_score_pivot_by_rank.csv")

    plot_repeat_significance_ranking(valid, summary, output_dir)
    plot_repeat_score_distribution(valid, summary, output_dir)
    plot_delay_tradeoff(summary, output_dir)

    print(output_dir)
    print(summary[summary_columns].round(4).to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Realistic16 repeat significance visualizations.")
    parser.add_argument("--input-csv", default=str(DEFAULT_INPUT_CSV))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--alpha", type=float, default=0.05)
    return parser.parse_args()


def main() -> None:
    build(parse_args())


if __name__ == "__main__":
    main()
