#!/usr/bin/env python3
"""Build final Dongho candidate significance figures from 300-repeat raw rows."""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib import pyplot as plt
from matplotlib.patches import Rectangle
from scipy import stats


ROOT = Path("/Users/junlee/Desktop/js")
INPUT_DIR = ROOT / "real_final csv"
B4_RAW = INPUT_DIR / "Final_result_all (1).csv"
B0_RAW = INPUT_DIR / "Final_baseline.csv"
SEMI_FINAL_RAW = INPUT_DIR / "Semi_FInal (1).csv"
OUT_DIR = ROOT / "results/figures/dongho_final_significance"

TOP3 = [
    "bo_r44_003_tl31_dt129_ge6_qr62_tau74",
    "bo_r32_003_tl88_dt140_ge24_qr24_tau82",
    "bo_r36_005_tl20_dt125_ge9_qr54_tau82",
]

LABELS = {
    TOP3[0]: "Best B4\nr44 tl31 dt129\nge6 qr62 tau74",
    TOP3[1]: "B4 #2\nr32 tl88 dt140\nge24 qr24 tau82",
    TOP3[2]: "B4 #3\nr36 tl20 dt125\nge9 qr54 tau82",
    "B0_no_signal_control": "B0\nNo signal control",
}

SHORT_LABELS = {
    TOP3[0]: "C1 Best B4",
    TOP3[1]: "C2 B4 #2",
    TOP3[2]: "C3 B4 #3",
    "B0_no_signal_control": "B0",
}

COLORS = {
    "best": "#E66A2C",
    "b4": "#2F80C5",
    "b0": "#8B96A6",
    "reject": "#E66A2C",
    "not_reject": "#D6DCE4",
    "grid": "#DDE5EE",
    "text": "#1F2D3D",
    "muted": "#657386",
}


def configure_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 160,
            "savefig.dpi": 280,
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 15,
            "axes.titleweight": "bold",
            "axes.labelsize": 11,
            "axes.edgecolor": "#C6D0DC",
            "axes.labelcolor": COLORS["text"],
            "xtick.color": COLORS["muted"],
            "ytick.color": COLORS["muted"],
            "text.color": COLORS["text"],
            "axes.unicode_minus": False,
            "figure.facecolor": "#FFFFFF",
            "axes.facecolor": "#FBFCFD",
            "savefig.facecolor": "#FFFFFF",
        }
    )


def failed_mask(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes", "failed"})


def read_sources() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    b4 = pd.read_csv(B4_RAW)
    b0 = pd.read_csv(B0_RAW)

    for frame, path in [(b4, B4_RAW), (b0, B0_RAW)]:
        missing = {"repeat_id", "objective_score", "D_E_sec", "D_G_sec", "T_actual_EMV_sec", "final_status", "failed"} - set(
            frame.columns
        )
        if missing:
            raise ValueError(f"{path} is missing required columns: {sorted(missing)}")
        frame["repeat_id"] = pd.to_numeric(frame["repeat_id"], errors="coerce")
        frame["objective_score"] = pd.to_numeric(frame["objective_score"], errors="coerce")
        frame["D_E_sec"] = pd.to_numeric(frame["D_E_sec"], errors="coerce")
        frame["D_G_sec"] = pd.to_numeric(frame["D_G_sec"], errors="coerce")
        frame["T_actual_EMV_sec"] = pd.to_numeric(frame["T_actual_EMV_sec"], errors="coerce")

    b4 = b4[b4["parameter_id"].isin(TOP3)].copy()
    b0 = b0.copy()
    b0["parameter_id"] = "B0_no_signal_control"

    def valid(frame: pd.DataFrame) -> pd.DataFrame:
        ok = (
            frame["final_status"].astype(str).str.strip().str.upper().eq("PASS")
            & ~failed_mask(frame["failed"])
            & frame["repeat_id"].notna()
            & frame["objective_score"].notna()
            & frame["D_E_sec"].notna()
            & frame["D_G_sec"].notna()
            & frame["T_actual_EMV_sec"].notna()
        )
        return frame.loc[ok].copy()

    b4 = valid(b4)
    b0 = valid(b0)
    combined = pd.concat([b4, b0], ignore_index=True, sort=False)
    combined["display_label"] = combined["parameter_id"].map(LABELS)
    combined["source_group"] = np.where(combined["parameter_id"].eq("B0_no_signal_control"), "B0", "B4")

    counts = combined.groupby("parameter_id")["repeat_id"].nunique().to_dict()
    expected = {pid: 300 for pid in TOP3}
    expected["B0_no_signal_control"] = 300
    if counts != expected:
        raise ValueError(f"Expected 300 repeats for each group, got {counts}")

    keep = [
        "source_group",
        "parameter_id",
        "display_label",
        "repeat_id",
        "objective_score",
        "D_E_sec",
        "D_G_sec",
        "T_actual_EMV_sec",
        "final_status",
        "failed",
    ]
    return b4, b0, combined[keep].sort_values(["source_group", "parameter_id", "repeat_id"])


def read_semifinal_source() -> pd.DataFrame:
    frame = pd.read_csv(SEMI_FINAL_RAW)
    missing = {"parameter_id", "repeat_id", "objective_score", "final_status", "failed"} - set(frame.columns)
    if missing:
        raise ValueError(f"{SEMI_FINAL_RAW} is missing required columns: {sorted(missing)}")
    frame["repeat_id"] = pd.to_numeric(frame["repeat_id"], errors="coerce")
    frame["objective_score"] = pd.to_numeric(frame["objective_score"], errors="coerce")
    ok = (
        frame["final_status"].astype(str).str.strip().str.upper().eq("PASS")
        & ~failed_mask(frame["failed"])
        & frame["repeat_id"].notna()
        & frame["objective_score"].notna()
    )
    out = frame.loc[ok, ["parameter_id", "repeat_id", "objective_score", "final_status", "failed"]].copy()
    if out["parameter_id"].nunique() != 16:
        raise ValueError(f"Expected 16 semifinal candidates, got {out['parameter_id'].nunique()}")
    return out


def pivot_scores(frame: pd.DataFrame, ids: list[str]) -> pd.DataFrame:
    pivot = frame[frame["parameter_id"].isin(ids)].pivot(
        index="repeat_id", columns="parameter_id", values="objective_score"
    )
    pivot = pivot[ids].dropna()
    if len(pivot) != 300:
        raise ValueError(f"Expected 300 paired repeats for {ids}, got {len(pivot)}")
    return pivot


def holm_adjust(p_values: list[float]) -> list[float]:
    adjusted = [np.nan] * len(p_values)
    order = np.argsort(p_values)
    running_max = 0.0
    m = len(p_values)
    for rank, idx in enumerate(order):
        value = min(1.0, (m - rank) * p_values[idx])
        running_max = max(running_max, value)
        adjusted[idx] = running_max
    return adjusted


def paired_test_rows(frame: pd.DataFrame, ids: list[str]) -> pd.DataFrame:
    pivot = pivot_scores(frame, ids)
    rows: list[dict[str, object]] = []
    for left, right in combinations(ids, 2):
        diff = pivot[right] - pivot[left]
        mean_diff = float(diff.mean())
        se = float(diff.std(ddof=1) / np.sqrt(len(diff)))
        ci_half = float(stats.t.ppf(0.975, len(diff) - 1) * se)
        t_result = stats.ttest_rel(pivot[right], pivot[left])
        wilcoxon = stats.wilcoxon(diff, zero_method="wilcox", alternative="two-sided")
        rows.append(
            {
                "left_parameter_id": left,
                "right_parameter_id": right,
                "left_label": LABELS[left].replace("\n", " "),
                "right_label": LABELS[right].replace("\n", " "),
                "n_common_repeats": int(len(diff)),
                "left_mean_score": float(pivot[left].mean()),
                "right_mean_score": float(pivot[right].mean()),
                "mean_diff_right_minus_left": mean_diff,
                "ci95_low": mean_diff - ci_half,
                "ci95_high": mean_diff + ci_half,
                "paired_t_stat": float(t_result.statistic),
                "paired_t_p": float(t_result.pvalue),
                "wilcoxon_p": float(wilcoxon.pvalue),
            }
        )
    out = pd.DataFrame(rows)
    out["paired_t_p_holm"] = holm_adjust(out["paired_t_p"].tolist())
    out["reject_h0_alpha_0_05_holm"] = out["paired_t_p_holm"] < 0.05
    return out


def summary_rows(combined: pd.DataFrame) -> pd.DataFrame:
    order = TOP3 + ["B0_no_signal_control"]
    rows = []
    for pid in order:
        subset = combined[combined["parameter_id"].eq(pid)]
        scores = subset["objective_score"]
        n = len(scores)
        se = float(scores.std(ddof=1) / np.sqrt(n))
        half = float(stats.t.ppf(0.975, n - 1) * se)
        rows.append(
            {
                "parameter_id": pid,
                "display_label": LABELS[pid].replace("\n", " "),
                "n": int(n),
                "mean_score": float(scores.mean()),
                "std_score": float(scores.std(ddof=1)),
                "median_score": float(scores.median()),
                "q1_score": float(scores.quantile(0.25)),
                "q3_score": float(scores.quantile(0.75)),
                "ci95_low": float(scores.mean() - half),
                "ci95_high": float(scores.mean() + half),
                "D_E_mean_sec": float(subset["D_E_sec"].mean()),
                "D_G_mean_sec": float(subset["D_G_sec"].mean()),
                "T_actual_EMV_mean_sec": float(subset["T_actual_EMV_sec"].mean()),
            }
        )
    return pd.DataFrame(rows)


def short_candidate_id(parameter_id: str) -> str:
    parts = str(parameter_id).split("_")
    if len(parts) >= 4 and parts[0] == "bo":
        return f"{parts[1]} {parts[3]} {parts[4]} {parts[5]} {parts[6]} {parts[7]}"
    return str(parameter_id)


def draw_top16_to_top3(semifinal: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for pid, group in semifinal.groupby("parameter_id", sort=False):
        scores = group["objective_score"].astype(float)
        n = len(scores)
        mean = float(scores.mean())
        half = float(stats.t.ppf(0.975, n - 1) * scores.std(ddof=1) / np.sqrt(n))
        rows.append(
            {
                "parameter_id": pid,
                "short_label": short_candidate_id(pid),
                "n": int(n),
                "mean_score": mean,
                "ci95_low": mean - half,
                "ci95_high": mean + half,
            }
        )
    summary = pd.DataFrame(rows).sort_values("mean_score", ascending=True, kind="mergesort").reset_index(drop=True)
    summary["rank_by_score"] = np.arange(1, len(summary) + 1)
    summary["selected_top3"] = summary["rank_by_score"].le(3)
    summary.to_csv(OUT_DIR / "material_0_top16_to_top3_ci_summary.csv", index=False)

    fig, ax = plt.subplots(figsize=(11.2, 8.4))
    fig.subplots_adjust(left=0.245, right=0.975, top=0.90, bottom=0.10)

    y_pos = np.arange(len(summary))[::-1]
    box_height = 0.44
    for y, (_, row) in zip(y_pos, summary.iterrows()):
        if row["rank_by_score"] == 1:
            color, alpha = COLORS["best"], 0.76
        elif row["rank_by_score"] <= 3:
            color, alpha = COLORS["b4"], 0.58
        else:
            color, alpha = "#B9C3CF", 0.38
        rect = Rectangle(
            (row["ci95_low"], y - box_height / 2),
            row["ci95_high"] - row["ci95_low"],
            box_height,
            facecolor=color,
            edgecolor="#2E3B4D",
            linewidth=1.05,
            alpha=alpha,
            zorder=3,
        )
        ax.add_patch(rect)
        ax.vlines(row["mean_score"], y - box_height * 0.72, y + box_height * 0.72, color="#17263A", linewidth=1.7, zorder=4)
        ax.scatter(row["mean_score"], y, s=38, color="#FFFFFF", edgecolor="#17263A", linewidth=1.05, zorder=5)
        ax.text(row["mean_score"] + 0.85, y, f"{row['mean_score']:.1f}", ha="left", va="center", fontsize=8.7)

    labels = [f"R{int(row.rank_by_score):02d}  {row.short_label}" for row in summary.itertuples(index=False)]
    ax.set_yticks(y_pos, labels=labels)
    ax.set_xlabel("Objective Score (lower is better)")
    ax.set_title("Top 16 Finalists: Mean Score with 95% CI", pad=14)
    ax.set_xlim(float(summary["ci95_low"].min() - 3), float(summary["ci95_high"].max() + 6))
    ax.set_ylim(-0.75, len(summary) - 0.25)
    ax.grid(True, axis="x", color=COLORS["grid"], linewidth=0.85)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="y", labelsize=8.6)

    fig.savefig(OUT_DIR / "material_0_top16_to_top3_ci_selection.png")
    fig.savefig(OUT_DIR / "material_0_top16_to_top3_ci_selection.svg")
    plt.close(fig)
    return summary


def draw_top16_welch_acceptance_band(semifinal: pd.DataFrame) -> pd.DataFrame:
    grouped = {pid: group["objective_score"].astype(float).to_numpy() for pid, group in semifinal.groupby("parameter_id")}
    rows = []
    for pid, scores in grouped.items():
        n = len(scores)
        mean = float(np.mean(scores))
        half = float(stats.t.ppf(0.975, n - 1) * np.std(scores, ddof=1) / np.sqrt(n))
        rows.append(
            {
                "parameter_id": pid,
                "short_label": short_candidate_id(pid),
                "n": int(n),
                "mean_score": mean,
                "std_score": float(np.std(scores, ddof=1)),
                "ci95_low": mean - half,
                "ci95_high": mean + half,
            }
        )

    summary = pd.DataFrame(rows).sort_values("mean_score", ascending=True, kind="mergesort").reset_index(drop=True)
    best = summary.iloc[0]
    best_scores = grouped[str(best["parameter_id"])]
    best_mean = float(best["mean_score"])
    best_var = float(np.var(best_scores, ddof=1))
    best_n = int(best["n"])

    p_values: list[float] = []
    welch_lows: list[float] = []
    welch_highs: list[float] = []
    welch_dfs: list[float] = []
    for row in summary.itertuples(index=False):
        scores = grouped[str(row.parameter_id)]
        n = int(row.n)
        var = float(np.var(scores, ddof=1))
        se_diff = float(np.sqrt(best_var / best_n + var / n))
        if str(row.parameter_id) == str(best["parameter_id"]):
            p_value = np.nan
            df = np.nan
            margin = 0.0
        else:
            test = stats.ttest_ind(scores, best_scores, equal_var=False)
            p_value = float(test.pvalue)
            numerator = (best_var / best_n + var / n) ** 2
            denominator = ((best_var / best_n) ** 2 / (best_n - 1)) + ((var / n) ** 2 / (n - 1))
            df = float(numerator / denominator)
            margin = float(stats.t.ppf(0.975, df) * se_diff)
        p_values.append(p_value)
        welch_lows.append(best_mean - margin)
        welch_highs.append(best_mean + margin)
        welch_dfs.append(df)

    summary["rank_by_score"] = np.arange(1, len(summary) + 1)
    summary["welch_p_vs_best"] = p_values
    summary["welch_df_vs_best"] = welch_dfs
    summary["best_compatible_low"] = welch_lows
    summary["best_compatible_high"] = welch_highs
    summary["reject_vs_best_alpha_0_05"] = summary["welch_p_vs_best"].lt(0.05).fillna(False)
    first_reject = summary[(summary["rank_by_score"] > 1) & summary["reject_vs_best_alpha_0_05"]]
    cut_rank = int(first_reject.iloc[0]["rank_by_score"] - 1) if not first_reject.empty else int(summary["rank_by_score"].max())
    summary["accepted_by_first_reject_rule"] = summary["rank_by_score"].le(cut_rank)
    summary.to_csv(OUT_DIR / "material_0_top16_welch_acceptance_band_tests.csv", index=False)

    fig, ax = plt.subplots(figsize=(11.4, 8.2))
    fig.subplots_adjust(left=0.250, right=0.985, top=0.90, bottom=0.10)
    y_pos = np.arange(len(summary))[::-1]
    band_height = 0.42

    for y, row in zip(y_pos, summary.itertuples(index=False)):
        if row.rank_by_score == 1:
            band_color, dot_color, alpha = COLORS["best"], COLORS["best"], 0.26
        elif row.accepted_by_first_reject_rule:
            band_color, dot_color, alpha = COLORS["b4"], COLORS["b4"], 0.24
        else:
            band_color, dot_color, alpha = "#B8C1CC", "#697789", 0.22

        if row.rank_by_score > 1:
            rect = Rectangle(
                (best_mean, y - band_height / 2),
                row.best_compatible_high - best_mean,
                band_height,
                facecolor=band_color,
                edgecolor="#7E8A98",
                linewidth=0.9,
                alpha=alpha,
                zorder=2,
            )
            ax.add_patch(rect)
            ax.vlines(row.best_compatible_high, y - band_height * 0.66, y + band_height * 0.66, color="#253447", linewidth=1.4, zorder=3)
        else:
            ax.axvline(best_mean, color=COLORS["best"], linewidth=2.0, zorder=2)

        marker_edge = COLORS["reject"] if row.reject_vs_best_alpha_0_05 else "#17263A"
        ax.scatter(row.mean_score, y, s=48, color="#FFFFFF", edgecolor=marker_edge, linewidth=1.35, zorder=5)
        ax.vlines(row.mean_score, y - band_height * 0.60, y + band_height * 0.60, color=marker_edge, linewidth=1.8, zorder=4)

        if row.rank_by_score == 1:
            label = f"{row.mean_score:.1f}"
        elif row.rank_by_score <= 4:
            label = f"{row.mean_score:.1f}  p={row.welch_p_vs_best:.3g}"
        else:
            label = f"{row.mean_score:.1f}"
        ax.text(row.mean_score + 0.75, y, label, ha="left", va="center", fontsize=8.8)

    if cut_rank < len(summary):
        boundary_y = y_pos[cut_rank - 1] - 0.50
        ax.axhline(boundary_y, color="#253447", linewidth=1.1, linestyle=(0, (4, 2)), zorder=1)
        ax.text(
            0.985,
            boundary_y + 0.12,
            f"Top {cut_rank} cut: first reject is R{cut_rank + 1:02d}",
            transform=ax.get_yaxis_transform(),
            ha="right",
            va="bottom",
            fontsize=9.3,
        )

    labels = [f"R{int(row.rank_by_score):02d}  {row.short_label}" for row in summary.itertuples(index=False)]
    ax.set_yticks(y_pos, labels=labels)
    ax.set_xlabel("Objective Score (lower is better)")
    ax.set_title("Top 16 Finalists: Welch Non-rejection Band vs Best", pad=14)
    x_min = best_mean - 2.4
    x_max = max(float(summary["mean_score"].max()), float(summary["best_compatible_high"].max())) + 5.0
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(-0.75, len(summary) - 0.25)
    ax.grid(True, axis="x", color=COLORS["grid"], linewidth=0.85)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="y", labelsize=8.6)

    handles = [
        Rectangle((0, 0), 1, 1, facecolor=COLORS["b4"], edgecolor="#7E8A98", alpha=0.24, label="Welch non-rejection band"),
        plt.Line2D([0], [0], marker="o", color="none", markerfacecolor="#FFFFFF", markeredgecolor="#17263A", markersize=6, label="Mean, not rejected"),
        plt.Line2D([0], [0], marker="o", color="none", markerfacecolor="#FFFFFF", markeredgecolor=COLORS["reject"], markersize=6, label="Mean, rejected"),
    ]
    ax.legend(handles=handles, frameon=False, loc="upper right", fontsize=8.8)

    fig.savefig(OUT_DIR / "material_0_top16_welch_acceptance_band.png")
    fig.savefig(OUT_DIR / "material_0_top16_welch_acceptance_band.svg")
    plt.close(fig)
    return summary


def build_top16_welch_vs_best_summary(semifinal: pd.DataFrame) -> pd.DataFrame:
    grouped = {pid: group["objective_score"].astype(float).to_numpy() for pid, group in semifinal.groupby("parameter_id")}
    rows = []
    for pid, scores in grouped.items():
        rows.append(
            {
                "parameter_id": pid,
                "short_label": short_candidate_id(pid),
                "n": int(len(scores)),
                "mean_score": float(np.mean(scores)),
                "std_score": float(np.std(scores, ddof=1)),
            }
        )

    summary = pd.DataFrame(rows).sort_values("mean_score", ascending=True, kind="mergesort").reset_index(drop=True)
    best = summary.iloc[0]
    best_scores = grouped[str(best["parameter_id"])]
    best_mean = float(best["mean_score"])
    best_var = float(np.var(best_scores, ddof=1))
    best_n = int(best["n"])

    p_values: list[float] = []
    diff_lows: list[float] = []
    diff_highs: list[float] = []
    score_lows: list[float] = []
    score_highs: list[float] = []
    dfs: list[float] = []
    for row in summary.itertuples(index=False):
        scores = grouped[str(row.parameter_id)]
        n = int(row.n)
        mean = float(row.mean_score)
        var = float(np.var(scores, ddof=1))
        if str(row.parameter_id) == str(best["parameter_id"]):
            half = float(stats.t.ppf(0.975, best_n - 1) * np.sqrt(best_var / best_n))
            p_value = np.nan
            df = np.nan
            low_diff = 0.0
            high_diff = 0.0
            low_score = mean - half
            high_score = mean + half
        else:
            se_diff = float(np.sqrt(best_var / best_n + var / n))
            numerator = (best_var / best_n + var / n) ** 2
            denominator = ((best_var / best_n) ** 2 / (best_n - 1)) + ((var / n) ** 2 / (n - 1))
            df = float(numerator / denominator)
            half = float(stats.t.ppf(0.975, df) * se_diff)
            diff = mean - best_mean
            low_diff = diff - half
            high_diff = diff + half
            low_score = best_mean + low_diff
            high_score = best_mean + high_diff
            p_value = float(stats.ttest_ind(scores, best_scores, equal_var=False).pvalue)
        p_values.append(p_value)
        dfs.append(df)
        diff_lows.append(low_diff)
        diff_highs.append(high_diff)
        score_lows.append(low_score)
        score_highs.append(high_score)

    summary["rank_by_score"] = np.arange(1, len(summary) + 1)
    summary["best_mean_score"] = best_mean
    summary["welch_p_vs_best"] = p_values
    summary["welch_df_vs_best"] = dfs
    summary["welch_diff_ci95_low"] = diff_lows
    summary["welch_diff_ci95_high"] = diff_highs
    summary["welch_score_ci95_low"] = score_lows
    summary["welch_score_ci95_high"] = score_highs
    summary["reject_vs_best_alpha_0_05"] = summary["welch_p_vs_best"].lt(0.05).fillna(False)
    first_reject = summary[(summary["rank_by_score"] > 1) & summary["reject_vs_best_alpha_0_05"]]
    cut_rank = int(first_reject.iloc[0]["rank_by_score"] - 1) if not first_reject.empty else int(summary["rank_by_score"].max())
    summary["accepted_by_first_reject_rule"] = summary["rank_by_score"].le(cut_rank)
    return summary


def draw_top16_welch_ci_pivot(semifinal: pd.DataFrame) -> pd.DataFrame:
    summary = build_top16_welch_vs_best_summary(semifinal)
    summary.to_csv(OUT_DIR / "material_0_top16_welch_ci_pivot_tests.csv", index=False)

    best_mean = float(summary.iloc[0]["best_mean_score"])
    fig, axes = plt.subplots(4, 4, figsize=(13.4, 8.2), sharex=True, sharey=True)
    fig.subplots_adjust(left=0.055, right=0.985, top=0.875, bottom=0.105, hspace=0.52, wspace=0.18)

    x_min = float(min(summary["welch_score_ci95_low"].min(), best_mean) - 2.0)
    x_max = float(max(summary["welch_score_ci95_high"].max(), summary["mean_score"].max()) + 2.0)

    for ax, row in zip(axes.flat, summary.itertuples(index=False)):
        if row.rank_by_score == 1:
            color = COLORS["best"]
            alpha = 0.74
            edge = "#2E3B4D"
            title = f"R01 Best"
        elif row.accepted_by_first_reject_rule:
            color = COLORS["b4"]
            alpha = 0.54
            edge = "#2E3B4D"
            title = f"R{int(row.rank_by_score):02d}  p={row.welch_p_vs_best:.3g}"
        else:
            color = "#C7CED8"
            alpha = 0.58
            edge = "#9AA5B1"
            title = f"R{int(row.rank_by_score):02d}  p={row.welch_p_vs_best:.3g}"

        ax.axvline(best_mean, color=COLORS["best"], linewidth=1.25, alpha=0.95, zorder=1)
        rect = Rectangle(
            (row.welch_score_ci95_low, -0.18),
            row.welch_score_ci95_high - row.welch_score_ci95_low,
            0.36,
            facecolor=color,
            edgecolor=edge,
            linewidth=0.95,
            alpha=alpha,
            zorder=3,
        )
        ax.add_patch(rect)
        marker_edge = COLORS["reject"] if row.reject_vs_best_alpha_0_05 else "#17263A"
        ax.vlines(row.mean_score, -0.30, 0.30, color=marker_edge, linewidth=1.6, zorder=4)
        ax.scatter(row.mean_score, 0, s=32, color="#FFFFFF", edgecolor=marker_edge, linewidth=1.05, zorder=5)
        ax.text(row.mean_score + 0.42, 0.32, f"{row.mean_score:.1f}", ha="left", va="bottom", fontsize=8.0)
        ax.text(0.02, 0.10, row.short_label, transform=ax.transAxes, ha="left", va="bottom", fontsize=7.0, color=COLORS["muted"])
        ax.set_title(title, fontsize=9.0, fontweight="bold", pad=6)
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(-0.52, 0.58)
        ax.set_yticks([])
        ax.grid(True, axis="x", color=COLORS["grid"], linewidth=0.75)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(axis="x", labelsize=8.0)

    for ax in axes[-1, :]:
        ax.set_xlabel("Objective Score (lower is better)", fontsize=9.0)

    handles = [
        plt.Line2D([0], [0], color=COLORS["best"], linewidth=1.7, label="Best mean"),
        Rectangle((0, 0), 1, 1, facecolor=COLORS["b4"], edgecolor="#2E3B4D", alpha=0.54, label="Welch 95% CI, not rejected"),
        Rectangle((0, 0), 1, 1, facecolor="#C7CED8", edgecolor="#9AA5B1", alpha=0.58, label="Welch 95% CI, rejected"),
    ]
    fig.legend(handles=handles, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 0.935), ncol=3, fontsize=8.6)
    fig.suptitle("Top 16 Finalists: Welch 95% CI vs Best (Pivot View)", fontsize=16, fontweight="bold", y=0.985)
    fig.savefig(OUT_DIR / "material_0_top16_welch_ci_pivot.png")
    fig.savefig(OUT_DIR / "material_0_top16_welch_ci_pivot.svg")
    plt.close(fig)
    return summary


def draw_top16_vertical_acceptance_interval(semifinal: pd.DataFrame) -> pd.DataFrame:
    summary = build_top16_welch_vs_best_summary(semifinal)
    summary.to_csv(OUT_DIR / "material_0_top16_vertical_acceptance_intervals.csv", index=False)

    best_mean = float(summary.iloc[0]["best_mean_score"])
    x = np.arange(len(summary)) + 1

    fig, ax = plt.subplots(figsize=(12.6, 6.0))
    fig.subplots_adjust(left=0.070, right=0.990, top=0.825, bottom=0.165)

    accepted = summary["accepted_by_first_reject_rule"].to_numpy(dtype=bool)
    ax.axvspan(0.5, float(accepted.sum()) + 0.5, color="#DDECF8", alpha=0.62, zorder=0)
    ax.axvspan(float(accepted.sum()) + 0.5, len(summary) + 0.5, color="#F1F3F6", alpha=0.72, zorder=0)
    ax.axvline(float(accepted.sum()) + 0.5, color="#253447", linewidth=1.1, linestyle=(0, (4, 2)), zorder=1)
    ax.axhline(best_mean, color=COLORS["best"], linewidth=2.1, zorder=2)

    for xpos, row in zip(x, summary.itertuples(index=False)):
        if row.rank_by_score == 1:
            color = COLORS["best"]
            marker_edge = "#17263A"
            alpha = 0.92
            linewidth = 2.5
        elif row.accepted_by_first_reject_rule:
            color = COLORS["b4"]
            marker_edge = "#17263A"
            alpha = 0.86
            linewidth = 2.2
        else:
            color = "#B8C1CC"
            marker_edge = COLORS["reject"]
            alpha = 0.82
            linewidth = 2.0

        ax.vlines(
            xpos,
            row.welch_score_ci95_low,
            row.welch_score_ci95_high,
            color=color,
            linewidth=8.8,
            alpha=0.62,
            zorder=3,
        )
        ax.vlines(
            xpos,
            row.welch_score_ci95_low,
            row.welch_score_ci95_high,
            color="#2B3848" if row.accepted_by_first_reject_rule else "#9AA5B1",
            linewidth=linewidth,
            alpha=alpha,
            zorder=4,
        )
        ax.scatter(xpos, row.mean_score, s=48, color="#FFFFFF", edgecolor=marker_edge, linewidth=1.25, zorder=5)

        if row.rank_by_score <= 4:
            label = f"{row.mean_score:.1f}" if row.rank_by_score == 1 else f"{row.mean_score:.1f}\np={row.welch_p_vs_best:.3g}"
            offsets = {
                1: (0.18, 0.35, "left"),
                2: (0.18, 0.40, "left"),
                3: (-0.34, 0.28, "right"),
                4: (0.18, 0.42, "left"),
            }
            dx, dy, ha = offsets[int(row.rank_by_score)]
            ax.text(xpos + dx, row.mean_score + dy, label, ha=ha, va="bottom", fontsize=7.8, zorder=6)
        elif row.rank_by_score in {8, 12, 16}:
            ax.text(xpos + 0.16, row.mean_score + 0.25, f"{row.mean_score:.1f}", ha="left", va="bottom", fontsize=7.7, color=COLORS["muted"], zorder=6)

    ax.text(
        0.988,
        best_mean + 0.15,
        "Best mean",
        ha="right",
        va="bottom",
        fontsize=9.0,
        color=COLORS["best"],
        transform=ax.get_yaxis_transform(),
    )
    y_min = float(min(summary["welch_score_ci95_low"].min(), summary["mean_score"].min()) - 2.0)
    y_max = float(max(summary["welch_score_ci95_high"].max(), summary["mean_score"].max()) + 2.2)
    ax.set_ylim(y_min, y_max)
    ax.set_xlim(0.5, len(summary) + 0.85)
    ax.text(2.0, y_max - 0.65, "Accepted vs Best", ha="center", va="top", fontsize=9.5, fontweight="bold", color="#245F93")
    ax.text(9.8, y_max - 0.65, "Rejected vs Best", ha="center", va="top", fontsize=9.5, fontweight="bold", color="#66717F")

    labels = [f"R{int(row.rank_by_score):02d}" for row in summary.itertuples(index=False)]
    ax.set_xticks(x, labels=labels)
    ax.set_ylabel("Objective Score (lower is better)")
    ax.set_xlabel("Candidate Rank")
    ax.set_title("Top 16 Finalists: Welch Acceptance Intervals vs Best", pad=13)
    ax.grid(True, axis="y", color=COLORS["grid"], linewidth=0.85)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="x", labelsize=8.8)

    handles = [
        plt.Line2D([0], [0], color=COLORS["best"], linewidth=2.1, label="Best mean"),
        plt.Line2D([0], [0], color=COLORS["b4"], linewidth=7.0, alpha=0.62, label="Welch 95% interval, accepted"),
        plt.Line2D([0], [0], color="#B8C1CC", linewidth=7.0, alpha=0.62, label="Welch 95% interval, rejected"),
    ]
    ax.legend(handles=handles, frameon=False, loc="upper left", bbox_to_anchor=(0.0, 1.035), ncol=3, fontsize=8.0)

    fig.savefig(OUT_DIR / "material_0_top16_vertical_acceptance_intervals.png")
    fig.savefig(OUT_DIR / "material_0_top16_vertical_acceptance_intervals.svg")
    plt.close(fig)
    return summary


def draw_top16_welch_difference_ci(semifinal: pd.DataFrame) -> pd.DataFrame:
    summary = build_top16_welch_vs_best_summary(semifinal)
    summary.to_csv(OUT_DIR / "material_0_top16_welch_difference_ci_tests.csv", index=False)

    x = np.arange(len(summary)) + 1
    accepted = summary["accepted_by_first_reject_rule"].to_numpy(dtype=bool)

    fig, ax = plt.subplots(figsize=(12.6, 6.0))
    fig.subplots_adjust(left=0.075, right=0.990, top=0.845, bottom=0.165)

    ax.axhline(0, color=COLORS["best"], linewidth=2.0, zorder=2)
    ax.axvspan(0.5, float(accepted.sum()) + 0.5, color="#DDECF8", alpha=0.62, zorder=0)
    ax.axvspan(float(accepted.sum()) + 0.5, len(summary) + 0.5, color="#F1F3F6", alpha=0.72, zorder=0)
    ax.axvline(float(accepted.sum()) + 0.5, color="#253447", linewidth=1.05, linestyle=(0, (4, 2)), zorder=1)

    for xpos, row in zip(x, summary.itertuples(index=False)):
        if row.rank_by_score == 1:
            color = COLORS["best"]
            marker_edge = "#17263A"
            line_alpha = 0.86
            low = high = 0.0
        elif row.accepted_by_first_reject_rule:
            color = COLORS["b4"]
            marker_edge = "#17263A"
            line_alpha = 0.82
            low = float(row.welch_diff_ci95_low)
            high = float(row.welch_diff_ci95_high)
        else:
            color = "#B8C1CC"
            marker_edge = COLORS["reject"]
            line_alpha = 0.78
            low = float(row.welch_diff_ci95_low)
            high = float(row.welch_diff_ci95_high)

        ax.vlines(xpos, low, high, color=color, linewidth=8.8, alpha=0.55, zorder=3)
        ax.vlines(xpos, low, high, color="#2B3848" if row.accepted_by_first_reject_rule else "#9AA5B1", linewidth=1.7, alpha=line_alpha, zorder=4)
        ax.scatter(
            xpos,
            float(row.mean_score - row.best_mean_score),
            s=48,
            color="#FFFFFF",
            edgecolor=marker_edge,
            linewidth=1.25,
            zorder=5,
        )

        if row.rank_by_score <= 4:
            if row.rank_by_score == 1:
                label = "0.0"
                dx, dy, ha = 0.16, 0.45, "left"
            else:
                label = f"{row.mean_score - row.best_mean_score:.1f}\np={row.welch_p_vs_best:.3g}"
                offsets = {
                    2: (0.16, 0.55, "left"),
                    3: (-0.28, 0.55, "right"),
                    4: (0.16, 0.55, "left"),
                }
                dx, dy, ha = offsets[int(row.rank_by_score)]
            ax.text(xpos + dx, float(row.mean_score - row.best_mean_score) + dy, label, ha=ha, va="bottom", fontsize=7.9, zorder=6)
        elif row.rank_by_score in {8, 12, 16}:
            ax.text(
                xpos + 0.14,
                float(row.mean_score - row.best_mean_score) + 0.35,
                f"{row.mean_score - row.best_mean_score:.1f}",
                ha="left",
                va="bottom",
                fontsize=7.4,
                color=COLORS["muted"],
                zorder=6,
            )

    y_min = float(min(summary["welch_diff_ci95_low"].min(), -2.0) - 1.2)
    y_max = float(max(summary["welch_diff_ci95_high"].max(), summary["mean_score"].sub(summary["best_mean_score"]).max()) + 2.2)
    ax.set_ylim(y_min, y_max)
    ax.set_xlim(0.5, len(summary) + 0.85)
    ax.text(2.0, y_max - 0.55, "Not rejected", ha="center", va="top", fontsize=9.5, fontweight="bold", color="#245F93")
    ax.text(9.8, y_max - 0.55, "Rejected vs Best", ha="center", va="top", fontsize=9.5, fontweight="bold", color="#66717F")
    ax.text(0.988, 0.25, "No difference", ha="right", va="bottom", fontsize=8.8, color=COLORS["best"], transform=ax.get_yaxis_transform())

    labels = [f"R{int(row.rank_by_score):02d}" for row in summary.itertuples(index=False)]
    ax.set_xticks(x, labels=labels)
    ax.set_ylabel("Mean Score Difference vs Best")
    ax.set_xlabel("Candidate Rank")
    ax.set_title("Top 16 Finalists: Welch 95% CI for Mean Difference vs Best", pad=13)
    ax.grid(True, axis="y", color=COLORS["grid"], linewidth=0.85)
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(axis="x", labelsize=8.8)

    handles = [
        plt.Line2D([0], [0], color=COLORS["best"], linewidth=2.0, label="Zero difference"),
        plt.Line2D([0], [0], color=COLORS["b4"], linewidth=7.0, alpha=0.55, label="CI includes zero"),
        plt.Line2D([0], [0], color="#B8C1CC", linewidth=7.0, alpha=0.55, label="CI excludes zero"),
    ]
    ax.legend(handles=handles, frameon=False, loc="upper left", bbox_to_anchor=(0.0, 1.035), ncol=3, fontsize=8.0)

    fig.savefig(OUT_DIR / "material_0_top16_welch_difference_ci.png")
    fig.savefig(OUT_DIR / "material_0_top16_welch_difference_ci.svg")
    plt.close(fig)
    return summary


def draw_top16_cumulative_mean_scores(semifinal: pd.DataFrame) -> pd.DataFrame:
    summary = build_top16_welch_vs_best_summary(semifinal)[["parameter_id", "short_label", "rank_by_score", "mean_score"]]
    rank_map = summary.set_index("parameter_id")["rank_by_score"].to_dict()
    short_map = summary.set_index("parameter_id")["short_label"].to_dict()

    rows = []
    for pid, group in semifinal.groupby("parameter_id", sort=False):
        ordered = group.sort_values("repeat_id").reset_index(drop=True)
        scores = ordered["objective_score"].astype(float)
        cumulative = scores.expanding().mean()
        for idx, (repeat_id, score, running_mean) in enumerate(
            zip(ordered["repeat_id"], scores, cumulative), start=1
        ):
            rows.append(
                {
                    "parameter_id": pid,
                    "rank_by_final_mean": int(rank_map[pid]),
                    "short_label": short_map[pid],
                    "repeat_id": int(repeat_id),
                    "repeat_count": idx,
                    "score": float(score),
                    "cumulative_mean_score": float(running_mean),
                }
            )
    running = pd.DataFrame(rows).sort_values(["rank_by_final_mean", "repeat_count"])
    running.to_csv(OUT_DIR / "material_0_top16_cumulative_mean_scores.csv", index=False)

    fig, ax = plt.subplots(figsize=(12.6, 6.0))
    fig.subplots_adjust(left=0.075, right=0.885, top=0.875, bottom=0.145)
    for pid, group in running.groupby("parameter_id", sort=False):
        rank = int(group["rank_by_final_mean"].iloc[0])
        if rank == 1:
            color, linewidth, alpha, zorder = COLORS["best"], 2.5, 0.96, 5
        elif rank <= 3:
            color, linewidth, alpha, zorder = COLORS["b4"], 2.15, 0.84, 4
        elif rank == 4:
            color, linewidth, alpha, zorder = "#4D5968", 2.05, 0.82, 3
        else:
            color, linewidth, alpha, zorder = "#AAB4C0", 1.15, 0.34, 2
        ax.plot(
            group["repeat_count"],
            group["cumulative_mean_score"],
            color=color,
            linewidth=linewidth,
            alpha=alpha,
            zorder=zorder,
        )

        final_x = float(group["repeat_count"].iloc[-1])
        final_y = float(group["cumulative_mean_score"].iloc[-1])
        if rank <= 4 or rank in {8, 12, 16}:
            ax.text(
                final_x + 2.0,
                final_y,
                f"R{rank:02d}",
                ha="left",
                va="center",
                fontsize=8.4 if rank <= 4 else 7.5,
                color=color if rank <= 4 else COLORS["muted"],
            )

    ax.set_title("Top 16 Finalists: Cumulative Mean Score by Repeat Count", pad=13)
    ax.set_xlabel("Repeat Count")
    ax.set_ylabel("Cumulative Mean Objective Score (lower is better)")
    ax.set_xlim(1, float(running["repeat_count"].max()) + 18)
    ax.set_ylim(145, 205)
    ax.grid(True, color=COLORS["grid"], linewidth=0.85)
    ax.spines[["top", "right"]].set_visible(False)
    handles = [
        plt.Line2D([0], [0], color=COLORS["best"], linewidth=2.5, label="R01 Best"),
        plt.Line2D([0], [0], color=COLORS["b4"], linewidth=2.15, label="R02-R03 selected"),
        plt.Line2D([0], [0], color="#4D5968", linewidth=2.05, label="R04 first reject"),
        plt.Line2D([0], [0], color="#AAB4C0", linewidth=1.5, alpha=0.7, label="R05-R16"),
    ]
    ax.legend(handles=handles, frameon=False, loc="upper right", fontsize=8.6)
    fig.savefig(OUT_DIR / "material_0_top16_cumulative_mean_scores.png")
    fig.savefig(OUT_DIR / "material_0_top16_cumulative_mean_scores.svg")
    plt.close(fig)

    focus = running[running["rank_by_final_mean"].le(4)].copy()
    fig, ax = plt.subplots(figsize=(12.6, 6.0))
    fig.subplots_adjust(left=0.075, right=0.975, top=0.875, bottom=0.145)
    for pid, group in focus.groupby("parameter_id", sort=False):
        rank = int(group["rank_by_final_mean"].iloc[0])
        if rank == 1:
            color, label = COLORS["best"], "R01 Best"
        elif rank <= 3:
            color, label = COLORS["b4"], f"R{rank:02d} selected"
        else:
            color, label = "#4D5968", "R04 first reject"
        ax.plot(group["repeat_count"], group["cumulative_mean_score"], color=color, linewidth=2.45, alpha=0.95, label=label)
        ax.scatter(
            [group["repeat_count"].iloc[-1]],
            [group["cumulative_mean_score"].iloc[-1]],
            s=42,
            color="#FFFFFF",
            edgecolor=color,
            linewidth=1.25,
            zorder=5,
        )
        ax.text(
            float(group["repeat_count"].iloc[-1]) + 1.6,
            float(group["cumulative_mean_score"].iloc[-1]),
            f"R{rank:02d}  {group['cumulative_mean_score'].iloc[-1]:.1f}",
            ha="left",
            va="center",
            fontsize=9.0,
            color=color,
        )
    ax.set_title("Top 4 Finalists: Cumulative Mean Score Stability", pad=13)
    ax.set_xlabel("Repeat Count")
    ax.set_ylabel("Cumulative Mean Objective Score (lower is better)")
    ax.set_xlim(1, float(focus["repeat_count"].max()) + 18)
    ax.set_ylim(145, 182)
    ax.grid(True, color=COLORS["grid"], linewidth=0.85)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper right", fontsize=8.8)
    fig.savefig(OUT_DIR / "material_0_top4_cumulative_mean_scores_zoom.png")
    fig.savefig(OUT_DIR / "material_0_top4_cumulative_mean_scores_zoom.svg")
    plt.close(fig)
    return running


def draw_top3_pairwise(pairwise: pd.DataFrame, b4: pd.DataFrame) -> None:
    best_rows = pairwise[pairwise["left_parameter_id"].eq(TOP3[0])].copy()
    best_rows["comparison_label"] = best_rows["right_parameter_id"].map(
        {
            TOP3[1]: "Best B4 vs B4 #2",
            TOP3[2]: "Best B4 vs B4 #3",
        }
    )
    best_rows.to_csv(OUT_DIR / "material_1_best_vs_other_finalists_p_tests.csv", index=False)

    summary_rows = []
    for pid in TOP3:
        scores = b4.loc[b4["parameter_id"].eq(pid), "objective_score"].astype(float)
        n = len(scores)
        mean = float(scores.mean())
        half = float(stats.t.ppf(0.975, n - 1) * scores.std(ddof=1) / np.sqrt(n))
        summary_rows.append(
            {
                "parameter_id": pid,
                "label": SHORT_LABELS[pid],
                "n": n,
                "mean": mean,
                "ci_low": mean - half,
                "ci_high": mean + half,
            }
        )
    summary = pd.DataFrame(summary_rows)

    fig, ax = plt.subplots(figsize=(10.8, 5.5))
    fig.subplots_adjust(left=0.135, right=0.965, top=0.78, bottom=0.16)

    y_pos = np.arange(len(TOP3))[::-1]
    box_height = 0.34
    for y, (_, row) in zip(y_pos, summary.iterrows()):
        color = COLORS["best"] if row["parameter_id"] == TOP3[0] else COLORS["b4"]
        alpha = 0.72 if row["parameter_id"] == TOP3[0] else 0.50
        rect = Rectangle(
            (row["ci_low"], y - box_height / 2),
            row["ci_high"] - row["ci_low"],
            box_height,
            facecolor=color,
            edgecolor="#2E3B4D",
            linewidth=1.25,
            alpha=alpha,
            zorder=3,
        )
        ax.add_patch(rect)
        ax.vlines(row["mean"], y - box_height * 0.72, y + box_height * 0.72, color="#17263A", linewidth=2.1, zorder=4)
        ax.scatter(row["mean"], y, s=58, color="#FFFFFF", edgecolor="#17263A", linewidth=1.2, zorder=5)
        ax.text(row["mean"], y + 0.31, f"{row['mean']:.1f}", ha="center", va="bottom", fontsize=10.4)

    x_min = float(summary["ci_low"].min() - 4)
    x_max_data = float(summary["ci_high"].max())
    x_max = x_max_data + 4.0
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(-0.65, 2.65)
    ax.set_yticks(y_pos, labels=["Best B4", "B4 #2", "B4 #3"])
    ax.set_xlabel("Objective Score (lower is better)")
    ax.set_title("Top Finalists: Mean Score with 95% CI and Paired P-tests", pad=14)
    ax.grid(True, axis="x", color=COLORS["grid"], linewidth=0.85)
    ax.spines[["top", "right"]].set_visible(False)

    fig.savefig(OUT_DIR / "material_1_top3_pairwise_hypothesis_tests.png")
    fig.savefig(OUT_DIR / "material_1_top3_pairwise_hypothesis_tests.svg")
    plt.close(fig)


def draw_best_vs_b0(combined: pd.DataFrame, best_stats: pd.DataFrame) -> None:
    best = TOP3[0]
    ids = ["B0_no_signal_control", best]
    pivot = pivot_scores(combined, ids)
    b0 = pivot["B0_no_signal_control"]
    best_scores = pivot[best]
    improvement = b0 - best_scores

    fig, (ax_box, ax_delta) = plt.subplots(
        1, 2, figsize=(12.8, 6.2), gridspec_kw={"width_ratios": [0.95, 1.05]}
    )
    fig.subplots_adjust(left=0.075, right=0.985, top=0.82, bottom=0.16, wspace=0.30)

    data = [b0.to_numpy(dtype=float), best_scores.to_numpy(dtype=float)]
    box = ax_box.boxplot(
        data,
        patch_artist=True,
        widths=0.48,
        showmeans=True,
        meanprops={"marker": "o", "markerfacecolor": "#FFFFFF", "markeredgecolor": "#17263A", "markersize": 6},
        medianprops={"color": "#17263A", "linewidth": 1.5},
        whiskerprops={"color": "#6B7788"},
        capprops={"color": "#6B7788"},
        flierprops={"marker": "o", "markersize": 2.4, "markerfacecolor": "#B6C0CE", "markeredgewidth": 0, "alpha": 0.55},
    )
    for patch, color in zip(box["boxes"], [COLORS["b0"], COLORS["best"]]):
        patch.set_facecolor(color)
        patch.set_alpha(0.72)
        patch.set_edgecolor("#344255")
        patch.set_linewidth(1.0)

    means = [float(b0.mean()), float(best_scores.mean())]
    ci_half = [
        float(stats.t.ppf(0.975, len(b0) - 1) * b0.std(ddof=1) / np.sqrt(len(b0))),
        float(stats.t.ppf(0.975, len(best_scores) - 1) * best_scores.std(ddof=1) / np.sqrt(len(best_scores))),
    ]
    ax_box.errorbar([1, 2], means, yerr=ci_half, fmt="none", ecolor="#17263A", elinewidth=1.8, capsize=5, zorder=4)
    ax_box.set_xticks([1, 2], labels=[LABELS[ids[0]], LABELS[ids[1]]])
    ax_box.set_ylabel("Objective Score (lower is better)")
    ax_box.set_title("Objective Score Distribution", pad=14)
    ax_box.grid(True, axis="y", color=COLORS["grid"], linewidth=0.85)
    ax_box.spines[["top", "right"]].set_visible(False)

    ax_delta.hist(improvement, bins=24, color="#CFE3F4", edgecolor="#6B9FCC", linewidth=0.8)
    mean_improvement = float(improvement.mean())
    se = float(improvement.std(ddof=1) / np.sqrt(len(improvement)))
    half = float(stats.t.ppf(0.975, len(improvement) - 1) * se)
    p_value = float(stats.ttest_rel(b0, best_scores).pvalue)
    ax_delta.axvline(0, color="#303A46", linewidth=1.1, linestyle="--", label="No improvement")
    ax_delta.axvline(mean_improvement, color=COLORS["best"], linewidth=2.2, label="Mean improvement")
    ax_delta.axvspan(mean_improvement - half, mean_improvement + half, color=COLORS["best"], alpha=0.16, label="95% CI")
    ax_delta.set_xlabel("B0 Score minus Best B4 Score")
    ax_delta.set_ylabel("Repeat Count")
    ax_delta.set_title("Paired Improvement over B0", pad=14)
    ax_delta.grid(True, axis="y", color=COLORS["grid"], linewidth=0.85)
    ax_delta.spines[["top", "right"]].set_visible(False)
    pct = 100.0 * mean_improvement / float(b0.mean())
    ax_delta.text(
        0.98,
        0.95,
        f"n=300 paired repeats\nMean improvement = {mean_improvement:.1f} score points\nImprovement vs B0 = {pct:.1f}%\nPaired t-test p = {p_value:.2e}",
        transform=ax_delta.transAxes,
        ha="right",
        va="top",
        fontsize=10,
        bbox={"boxstyle": "round,pad=0.35", "facecolor": "#FFFFFF", "edgecolor": "#D7DEE8", "alpha": 0.96},
    )
    ax_delta.legend(frameon=False, loc="upper left")

    fig.suptitle("Best Final Candidate vs B0 Baseline", fontsize=16, fontweight="bold")
    fig.savefig(OUT_DIR / "material_2_best_vs_b0_objective_comparison.png")
    fig.savefig(OUT_DIR / "material_2_best_vs_b0_objective_comparison.svg")
    plt.close(fig)

    best_stats.to_csv(OUT_DIR / "material_2_best_vs_b0_group_summary.csv", index=False)
    pd.DataFrame(
        [
            {
                "left_parameter_id": "B0_no_signal_control",
                "right_parameter_id": best,
                "n_common_repeats": int(len(improvement)),
                "b0_mean_score": float(b0.mean()),
                "best_b4_mean_score": float(best_scores.mean()),
                "mean_improvement_b0_minus_best": mean_improvement,
                "improvement_ci95_low": mean_improvement - half,
                "improvement_ci95_high": mean_improvement + half,
                "improvement_pct_vs_b0": pct,
                "paired_t_p": p_value,
                "wilcoxon_p": float(stats.wilcoxon(improvement, zero_method="wilcox", alternative="two-sided").pvalue),
            }
        ]
    ).to_csv(OUT_DIR / "material_2_best_vs_b0_test.csv", index=False)


def main() -> None:
    configure_style()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    b4, b0, combined = read_sources()
    semifinal = read_semifinal_source()
    combined.to_csv(OUT_DIR / "dongho_final_300_repeat_clean_source.csv", index=False)
    semifinal.to_csv(OUT_DIR / "material_0_top16_to_top3_clean_source.csv", index=False)
    draw_top16_to_top3(semifinal)
    draw_top16_welch_acceptance_band(semifinal)
    draw_top16_welch_ci_pivot(semifinal)
    draw_top16_vertical_acceptance_interval(semifinal)
    draw_top16_welch_difference_ci(semifinal)
    draw_top16_cumulative_mean_scores(semifinal)

    pairwise = paired_test_rows(b4, TOP3)
    pairwise.to_csv(OUT_DIR / "material_1_top3_pairwise_tests.csv", index=False)
    draw_top3_pairwise(pairwise, b4)

    group_summary = summary_rows(combined)
    group_summary.to_csv(OUT_DIR / "dongho_final_group_summary.csv", index=False)
    draw_best_vs_b0(combined, group_summary[group_summary["parameter_id"].isin(["B0_no_signal_control", TOP3[0]])])

    print(f"Wrote figures and source CSVs to {OUT_DIR}")


if __name__ == "__main__":
    main()
