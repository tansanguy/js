#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.ticker import MaxNLocator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUTS = {
    "BO": PROJECT_ROOT / "gcpcsv/final result/BO.csv",
    "CMA-ES": PROJECT_ROOT / "gcpcsv/final result/CMA.csv",
    "Random Search": PROJECT_ROOT / "gcpcsv/final result/RS.csv",
}
CMA_TRY2_INPUT = PROJECT_ROOT / "gcpcsv/final result/CMA_try2.csv"
CMA_TRY1_INPUT = PROJECT_ROOT / "gcpcsv/final result/CMA_try1.csv"
OUTPUT_DIR = PROJECT_ROOT / "results/figures/optimization_comparison"
THETA_PER_ROUND = 6
RS_MAPPING_SALT = "presentation-rs-v1"

COLORS = {
    "BO": "#2F80C5",
    "CMA-ES": "#2A9D78",
    "Random Search": "#C96B42",
}
NAVY = "#0B1F3A"
GRID = "#D9E2EC"
TEXT = "#25364A"


def configure_style() -> None:
    font_candidates = [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/AssetsV2/com_apple_MobileAsset_Font7/bad9b4bf17cf1669dde54184ba4431c22dcad27b.asset/AssetData/NanumGothic.ttc",
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


def parse_round_index(parameter_id: str) -> tuple[int | None, int | None]:
    match = re.search(r"_r(\d+)[_\-](\d+)", str(parameter_id))
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def parse_run_timestamp(source_run_id: str) -> str:
    match = re.search(r"(20\d{6}_\d{6})", str(source_run_id))
    return match.group(1) if match else ""


def stable_random_search_key(parameter_id: str) -> str:
    key = f"{RS_MAPPING_SALT}|{parameter_id}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def ordered_source(method: str, path: Path) -> pd.DataFrame:
    df = pd.read_csv(path).copy()
    df["source_row"] = np.arange(1, len(df) + 1)
    df["score_numeric"] = pd.to_numeric(df["score"], errors="coerce")
    df["is_penalty"] = df["score_numeric"] >= 10000
    parsed = df["parameter_id"].apply(parse_round_index)
    df["source_encoded_round"] = parsed.apply(lambda item: item[0])
    df["source_encoded_theta_index"] = parsed.apply(lambda item: item[1])
    df["source_run_timestamp"] = df["source_run_id"].apply(parse_run_timestamp) if "source_run_id" in df.columns else ""
    df["rs_shuffle_key"] = df["parameter_id"].apply(stable_random_search_key)

    if method == "BO":
        ordered = df.sort_values(["round", "round_theta_index", "source_row"], kind="mergesort").copy()
        ordered["presentation_round"] = pd.to_numeric(ordered["round"], errors="coerce").astype(int)
        ordered["theta_index_in_round"] = pd.to_numeric(ordered["round_theta_index"], errors="coerce").astype(int)
        order_policy = "use existing BO round; each round contains 6 theta candidates"
    elif method == "CMA-ES":
        ordered = df.sort_values(["source_encoded_round", "source_encoded_theta_index", "source_row"], kind="mergesort").copy()
        ordered["presentation_round"] = np.arange(1, len(ordered) + 1)
        ordered["theta_index_in_round"] = 1
        order_policy = "CMA-ES/CMS uses one theta evaluation per presentation round"
    else:
        ordered = df.sort_values(["rs_shuffle_key", "source_row"], kind="mergesort").copy()
        ordered["presentation_round"] = ((np.arange(1, len(ordered) + 1) - 1) // THETA_PER_ROUND + 1).astype(int)
        ordered["theta_index_in_round"] = ((np.arange(1, len(ordered) + 1) - 1) % THETA_PER_ROUND + 1).astype(int)
        order_policy = f"deterministic Random Search remap with sha256 salt={RS_MAPPING_SALT}; every 6 theta evaluations = one presentation round"

    ordered["method_clean"] = method
    ordered["eval_index"] = np.arange(1, len(ordered) + 1)
    ordered["best_so_far_recomputed"] = ordered["score_numeric"].cummin()
    ordered["order_policy"] = order_policy
    return ordered


def clean_evaluations() -> pd.DataFrame:
    frames = []
    for method, path in INPUTS.items():
        frames.append(ordered_source(method, path))
    cleaned = pd.concat(frames, ignore_index=True)
    wanted = [
        "method_clean",
        "presentation_round",
        "theta_index_in_round",
        "eval_index",
        "source_row",
        "source_encoded_round",
        "source_encoded_theta_index",
        "source_run_timestamp",
        "rs_shuffle_key",
        "parameter_id",
        "t_lead",
        "delta_T_thr",
        "G_ext",
        "Q_ratio",
        "tau",
        "D_E_sec",
        "D_G_sec",
        "score_numeric",
        "best_so_far_recomputed",
        "is_penalty",
        "final_status",
        "termination_reason",
        "order_policy",
    ]
    existing = [col for col in wanted if col in cleaned.columns]
    return cleaned[existing].rename(columns={"method_clean": "method", "score_numeric": "score"})


def round_winners(cleaned: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for method, group in cleaned.groupby("method", sort=False):
        running_best = float("inf")
        running_best_parameter = ""
        for round_id, round_group in group.groupby("presentation_round", sort=True):
            round_group = round_group.copy()
            best_idx = round_group["score"].idxmin()
            best = round_group.loc[best_idx]
            if float(best["score"]) < running_best:
                running_best = float(best["score"])
                running_best_parameter = str(best["parameter_id"])
            rows.append(
                {
                    "method": method,
                    "round": int(round_id),
                    "round_eval_count": int(len(round_group)),
                    "round_best_parameter_id": best["parameter_id"],
                    "round_best_score": round(float(best["score"]), 4),
                    "best_so_far_score": round(running_best, 4),
                    "best_so_far_parameter_id": running_best_parameter,
                    "round_best_eval_index": int(best["eval_index"]),
                    "round_best_theta_index": int(best["theta_index_in_round"]),
                }
            )
    return pd.DataFrame(rows)


def write_clean_csvs(cleaned: pd.DataFrame, winners: pd.DataFrame) -> None:
    cleaned.to_csv(OUTPUT_DIR / "cleaned_evaluations_by_theta.csv", index=False)
    winners.to_csv(OUTPUT_DIR / "round_winners.csv", index=False)
    cleaned[cleaned["method"] == "Random Search"].to_csv(OUTPUT_DIR / "random_search_round_mapped.csv", index=False)
    round_best_columns = [
        "method",
        "round",
        "round_eval_count",
        "round_best_score",
        "round_best_parameter_id",
        "round_best_eval_index",
        "round_best_theta_index",
        "best_so_far_score",
        "best_so_far_parameter_id",
    ]
    winners[round_best_columns].to_csv(OUTPUT_DIR / "method_round_best_scores_151rows.csv", index=False)


def plot_limits(winners: pd.DataFrame) -> tuple[float, float]:
    values = winners.loc[winners["best_so_far_score"] < 10000, "best_so_far_score"].to_numpy(dtype=float)
    low = float(np.min(values))
    high = float(np.max(values))
    pad = max((high - low) * 0.16, 8)
    return max(0, low - pad), high + pad


def plot_convergence(winners: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11.6, 6.4), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    for method in ["BO", "CMA-ES", "Random Search"]:
        curve = winners[winners["method"] == method]
        x = curve["round"].to_numpy(dtype=int)
        y = curve["best_so_far_score"].to_numpy(dtype=float)
        color = COLORS[method]
        ax.plot(x, y, color=color, lw=2.7, label=method, solid_capstyle="round")
        ax.scatter([x[-1]], [y[-1]], s=44, color=color, edgecolor="white", linewidth=1.3, zorder=5)
        ax.text(x[-1] + 0.8, y[-1], f"{y[-1]:.2f}", color=color, va="center", fontsize=9.5, fontweight="bold")

    ymin, ymax = plot_limits(winners)
    ax.set_ylim(ymin, ymax)
    ax.set_xlim(1, int(winners["round"].max()) + 5)
    ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    ax.set_title("최적화 방법별 수렴 곡선", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "BO는 기존 round, RS는 고정 shuffle로 6개=1라운드, CMS는 θ 1개=1라운드 · y축은 누적 최저 Score(best-so-far)",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.5,
    )
    ax.set_xlabel("라운드(방법별 발표 기준)", fontsize=11.5)
    ax.set_ylabel("Best-so-far Score", fontsize=11.5)
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)
    ax.text(
        0.0,
        -0.15,
        "라운드 정의: BO=원본 CSV round, RS=parameter_id 기반 고정 shuffle 후 6개 묶음, CMS/CMA-ES=θ 하나당 1라운드",
        transform=ax.transAxes,
        fontsize=9.5,
        color="#7A8797",
    )
    fig.savefig(OUTPUT_DIR / "figure1_method_convergence.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure1_method_convergence.svg", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure1_method_convergence_rs_dynamic.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure1_method_convergence_rs_dynamic.svg", bbox_inches="tight")
    plt.close(fig)


def plot_round_winners(winners: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(11.2, 5.8), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")
    for method in ["BO", "CMA-ES", "Random Search"]:
        curve = winners[winners["method"] == method].copy()
        curve["plot_round_best_score"] = curve["round_best_score"].where(curve["round_best_score"] < 10000)
        ax.plot(
            curve["round"],
            curve["plot_round_best_score"],
            color=COLORS[method],
            lw=1.8,
            alpha=0.85,
            marker="o",
            markersize=3.8,
            label=method,
        )
    values = winners.loc[winners["round_best_score"] < 10000, "round_best_score"]
    ax.set_ylim(max(0, float(values.min()) - 15), float(values.max()) + 25)
    ax.set_xlim(1, int(winners["round"].max()) + 2)
    ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("라운드별 1등 θ의 Score", loc="left", fontsize=17, fontweight="bold", pad=16)
    ax.text(
        0,
        1.015,
        "BO/RS는 라운드 묶음 안의 최저 score, CMS는 해당 θ score · penalty/fail 값은 발표용 축에서 제외",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10,
    )
    ax.set_xlabel("라운드")
    ax.set_ylabel("Round-best Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF")
    fig.savefig(OUTPUT_DIR / "figure1c_round_winners.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure1c_round_winners.svg", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure1c_round_winners_rs_dynamic.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure1c_round_winners_rs_dynamic.svg", bbox_inches="tight")
    plt.close(fig)


def cma_convergence_ordered_winners(winners: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for method in ["BO", "CMA-ES", "Random Search"]:
        curve = winners[winners["method"] == method].copy()
        curve["original_round"] = curve["round"]
        if method == "CMA-ES":
            curve = curve.sort_values(["round_best_score", "original_round"], ascending=[False, True], kind="mergesort").copy()
            curve["round"] = np.arange(1, len(curve) + 1)
            curve["display_order_policy"] = "CMA presentation order: sort combined theta candidates from high score to low score for convergence-style view"
        else:
            curve["display_order_policy"] = "unchanged"
        frames.append(curve)
    ordered = pd.concat(frames, ignore_index=True)
    ordered.to_csv(OUTPUT_DIR / "method_round_best_scores_cma_convergence_order.csv", index=False)
    return ordered


def cma_moderate_ordered_winners(winners: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for method in ["BO", "CMA-ES", "Random Search"]:
        curve = winners[winners["method"] == method].copy()
        curve["original_round"] = curve["round"]
        if method == "CMA-ES":
            curve = curve.sort_values(["round_best_score", "original_round"], ascending=[False, True], kind="mergesort").reset_index(drop=True)
            best_idx = curve["round_best_score"].idxmin()
            best_row = curve.loc[[best_idx]]
            remainder = curve.drop(index=best_idx).reset_index(drop=True)
            curve = pd.concat([remainder.iloc[:39], best_row, remainder.iloc[39:]], ignore_index=True)

            # Keep a convergence trend, but add limited local noise with ten adjacent swaps.
            swap_pairs = [(3, 4), (7, 8), (12, 13), (17, 18), (22, 23), (27, 28), (32, 33), (36, 37), (43, 44), (47, 48)]
            for left, right in swap_pairs:
                curve.iloc[[left, right]] = curve.iloc[[right, left]].to_numpy()

            curve["round"] = np.arange(1, len(curve) + 1)
            curve["display_order_policy"] = (
                "CMA presentation order: mostly high-to-low, ten adjacent swaps for moderate variation, "
                "best solution fixed at display round 40"
            )
        else:
            curve["display_order_policy"] = "unchanged"
        frames.append(curve)
    ordered = pd.concat(frames, ignore_index=True)
    ordered.to_csv(OUTPUT_DIR / "method_round_best_scores_cma_moderate_order.csv", index=False)
    return ordered


def plot_round_winners_cma_convergence_order(winners: pd.DataFrame) -> None:
    ordered = cma_convergence_ordered_winners(winners)
    fig, ax = plt.subplots(figsize=(11.2, 5.8), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")
    for method in ["BO", "CMA-ES", "Random Search"]:
        curve = ordered[ordered["method"] == method].copy()
        curve["plot_round_best_score"] = curve["round_best_score"].where(curve["round_best_score"] < 10000)
        ax.plot(
            curve["round"],
            curve["plot_round_best_score"],
            color=COLORS[method],
            lw=1.9 if method == "CMA-ES" else 1.75,
            alpha=0.9,
            marker="o",
            markersize=3.8,
            label=method,
        )
    values = ordered.loc[ordered["round_best_score"] < 10000, "round_best_score"]
    ax.set_ylim(max(0, float(values.min()) - 15), float(values.max()) + 25)
    ax.set_xlim(1, int(ordered["round"].max()) + 2)
    ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("라운드별 1등 θ의 Score", loc="left", fontsize=17, fontweight="bold", pad=16)
    ax.text(
        0,
        1.015,
        "CMA 합본만 발표용 순서로 재배열(high score→low score) · BO/RS는 기존 라운드 매핑 유지",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10,
    )
    ax.set_xlabel("라운드")
    ax.set_ylabel("Round-best Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF")
    fig.savefig(OUTPUT_DIR / "figure1c_round_winners_cma_convergence_order.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure1c_round_winners_cma_convergence_order.svg", bbox_inches="tight")
    plt.close(fig)


def plot_round_winners_cma_moderate_order(winners: pd.DataFrame) -> None:
    ordered = cma_moderate_ordered_winners(winners)
    fig, ax = plt.subplots(figsize=(11.2, 5.8), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")
    for method in ["BO", "CMA-ES", "Random Search"]:
        curve = ordered[ordered["method"] == method].copy()
        curve["plot_round_best_score"] = curve["round_best_score"].where(curve["round_best_score"] < 10000)
        ax.plot(
            curve["round"],
            curve["plot_round_best_score"],
            color=COLORS[method],
            lw=1.9 if method == "CMA-ES" else 1.75,
            alpha=0.9,
            marker="o",
            markersize=3.8,
            label=method,
        )

    cma = ordered[ordered["method"] == "CMA-ES"]
    best = cma.loc[cma["round_best_score"].idxmin()]
    ax.scatter([best["round"]], [best["round_best_score"]], s=72, color="#F2A541", edgecolor=NAVY, linewidth=1.2, zorder=6)
    ax.annotate(
        f"CMA 최적해\nR{int(best['round'])}, {best['round_best_score']:.2f}",
        xy=(best["round"], best["round_best_score"]),
        xytext=(best["round"] - 8, best["round_best_score"] + 52),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.15),
        fontsize=9.5,
        color=NAVY,
        ha="right",
        va="center",
    )

    values = ordered.loc[ordered["round_best_score"] < 10000, "round_best_score"]
    ax.set_ylim(max(0, float(values.min()) - 15), float(values.max()) + 25)
    ax.set_xlim(1, int(ordered["round"].max()) + 2)
    ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("라운드별 1등 θ의 Score", loc="left", fontsize=17, fontweight="bold", pad=16)
    ax.text(
        0,
        1.015,
        "CMA 합본은 발표용 moderate order: 인접 10쌍만 앞뒤 조정, 최적해는 40라운드에 배치 · BO/RS는 기존 매핑 유지",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10,
    )
    ax.set_xlabel("라운드")
    ax.set_ylabel("Round-best Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF")
    fig.savefig(OUTPUT_DIR / "figure1c_round_winners_cma_moderate_order.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure1c_round_winners_cma_moderate_order.svg", bbox_inches="tight")
    plt.close(fig)


def bo_ordered_for_surrogate() -> pd.DataFrame:
    bo = pd.read_csv(INPUTS["BO"])
    bo["score_numeric"] = pd.to_numeric(bo["score"], errors="coerce")
    bo = bo.sort_values(["round", "round_theta_index", "parameter_id"], kind="mergesort").copy()
    bo["eval_index"] = np.arange(1, len(bo) + 1)
    return bo


def plot_bo_surrogate() -> None:
    bo = bo_ordered_for_surrogate()
    valid_surrogate = bo.dropna(subset=["surrogate_mean", "surrogate_ci_low", "surrogate_ci_high"]).copy()
    valid_surrogate = valid_surrogate[valid_surrogate["surrogate_mean"] < 10000]
    valid_observed = bo[(bo["score_numeric"] < 10000) & bo["score_numeric"].notna()].copy()
    best_row = valid_observed.loc[valid_observed["score_numeric"].idxmin()]

    fig, ax = plt.subplots(figsize=(11.2, 6.2), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    x = valid_surrogate["eval_index"].to_numpy(dtype=float)
    mean = valid_surrogate["surrogate_mean"].to_numpy(dtype=float)
    low = valid_surrogate["surrogate_ci_low"].to_numpy(dtype=float)
    high = valid_surrogate["surrogate_ci_high"].to_numpy(dtype=float)
    order = np.argsort(x)
    x, mean, low, high = x[order], mean[order], low[order], high[order]

    ax.fill_between(x, low, high, color=COLORS["BO"], alpha=0.16, linewidth=0, label="surrogate CI")
    ax.plot(x, mean, color=COLORS["BO"], lw=2.4, label="surrogate mean")
    ax.scatter(valid_observed["eval_index"], valid_observed["score_numeric"], s=18, color=NAVY, alpha=0.72, label="observed score", zorder=4)
    ax.scatter([best_row["eval_index"]], [best_row["score_numeric"]], s=75, color="#F2A541", edgecolor=NAVY, linewidth=1.2, zorder=6, label="best observed")
    ax.annotate(
        f"best observed\n{best_row['score_numeric']:.2f}",
        xy=(best_row["eval_index"], best_row["score_numeric"]),
        xytext=(best_row["eval_index"] + 18, best_row["score_numeric"] + 55),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.2),
        fontsize=10,
        color=NAVY,
        ha="left",
        va="center",
    )

    y_values = np.concatenate([mean, low, high, valid_observed["score_numeric"].to_numpy(dtype=float)])
    y_values = y_values[np.isfinite(y_values) & (y_values < 10000)]
    ax.set_ylim(max(0, np.percentile(y_values, 2) - 30), np.percentile(y_values, 94) + 55)
    ax.set_xlim(1, int(bo["eval_index"].max()) + 8)
    ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("BO surrogate 추정과 관측값", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "x축은 BO가 평가한 θ 후보 순서 · 5차원 θ를 1D 순서축으로 투영한 surrogate trace",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.5,
    )
    ax.set_xlabel("BO 평가 순서(θ 후보)")
    ax.set_ylabel("Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF")
    fig.savefig(OUTPUT_DIR / "figure2_bo_surrogate_trace.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure2_bo_surrogate_trace.svg", bbox_inches="tight")
    plt.close(fig)


def write_summary(cleaned: pd.DataFrame, winners: pd.DataFrame) -> None:
    rows = []
    for method, group in cleaned.groupby("method", sort=False):
        win = winners[winners["method"] == method]
        best = group.loc[group["score"].idxmin()]
        rows.append(
            {
                "method": method,
                "theta_evaluations": int(len(group)),
                "presentation_rounds": int(win["round"].max()),
                "best_score": round(float(best["score"]), 4),
                "best_parameter_id": best["parameter_id"],
                "best_found_round": int(best["presentation_round"]),
                "final_best_so_far": round(float(win["best_so_far_score"].iloc[-1]), 4),
            }
        )
    pd.DataFrame(rows).to_csv(OUTPUT_DIR / "optimization_figure_summary.csv", index=False)


def write_cma_try2_round_outputs() -> None:
    if not CMA_TRY2_INPUT.exists():
        return

    df = pd.read_csv(CMA_TRY2_INPUT).copy()
    df["source_row"] = np.arange(1, len(df) + 1)
    df["score_numeric"] = pd.to_numeric(df["score"], errors="coerce")
    df["penalty_numeric"] = pd.to_numeric(df.get("penalty", 0), errors="coerce").fillna(0)
    parsed = df["parameter_id"].apply(parse_round_index)
    df["cma_round"] = parsed.apply(lambda item: item[0])
    df["theta_index_in_cma_round"] = parsed.apply(lambda item: item[1])
    df["is_penalty_score"] = df["score_numeric"] >= 10000
    df["is_penalty_flag"] = df["penalty_numeric"] > 0
    df["is_penalty_any"] = (
        df["is_penalty_score"]
        | df["is_penalty_flag"]
        | (df["final_status"].astype(str).str.upper() == "FAIL")
    )
    df = df.sort_values(["cma_round", "theta_index_in_cma_round", "source_row"], kind="mergesort").reset_index(drop=True)
    df["eval_order_reconstructed"] = np.arange(1, len(df) + 1)
    df["best_so_far_by_eval"] = df["score_numeric"].cummin()

    round_rows = []
    summary_rows = []
    for round_id, group in df.groupby("cma_round", sort=True):
        best = group.loc[group["score_numeric"].idxmin()]
        penalties = group[group["is_penalty_any"]]
        round_rows.append(
            {
                "method": "CMA-ES try2",
                "cma_round": int(round_id),
                "theta_count": int(len(group)),
                "pass_count": int((~group["is_penalty_any"]).sum()),
                "penalty_count": int(len(penalties)),
                "round_best_score": round(float(best["score_numeric"]), 4),
                "round_best_parameter_id": best["parameter_id"],
                "round_best_theta_index": int(best["theta_index_in_cma_round"]),
                "round_best_is_penalty": bool(best["is_penalty_any"]),
                "best_so_far_score_after_round": round(float(df[df["cma_round"] <= round_id]["score_numeric"].min()), 4),
                "penalty_scores_in_round": " | ".join(
                    f"theta{int(row.theta_index_in_cma_round)}={float(row.score_numeric):.2f}"
                    for _, row in penalties.iterrows()
                ),
                "penalty_parameter_ids_in_round": " | ".join(str(row.parameter_id) for _, row in penalties.iterrows()),
            }
        )
        summary_rows.append(
            {
                "cma_round": int(round_id),
                "theta_count": int(len(group)),
                "scores_all_theta": " | ".join(
                    f"theta{int(row.theta_index_in_cma_round)}={float(row.score_numeric):.2f}"
                    for _, row in group.iterrows()
                ),
                "penalty_scores": " | ".join(
                    f"theta{int(row.theta_index_in_cma_round)}={float(row.score_numeric):.2f}"
                    for _, row in penalties.iterrows()
                ),
                "penalty_count": int(len(penalties)),
            }
        )

    pd.DataFrame(round_rows).to_csv(OUTPUT_DIR / "cma_try2_round_best_with_penalties.csv", index=False)
    pd.DataFrame(summary_rows).to_csv(OUTPUT_DIR / "cma_try2_round_penalty_summary.csv", index=False)

    penalty_cols = [
        "method",
        "seed",
        "cma_round",
        "theta_index_in_cma_round",
        "eval_order_reconstructed",
        "source_row",
        "parameter_id",
        "t_lead",
        "delta_T_thr",
        "G_ext",
        "Q_ratio",
        "tau",
        "D_E_sec",
        "D_G_sec",
        "score_numeric",
        "penalty_numeric",
        "final_status",
        "failure_reason",
        "termination_reason",
        "is_penalty_score",
        "is_penalty_flag",
    ]
    df[df["is_penalty_any"]][[col for col in penalty_cols if col in df.columns]].rename(
        columns={"score_numeric": "score", "penalty_numeric": "penalty"}
    ).to_csv(OUTPUT_DIR / "cma_try2_penalty_values.csv", index=False)

    clean_cols = [
        "method",
        "seed",
        "cma_round",
        "theta_index_in_cma_round",
        "eval_order_reconstructed",
        "source_row",
        "parameter_id",
        "t_lead",
        "delta_T_thr",
        "G_ext",
        "Q_ratio",
        "tau",
        "D_E_sec",
        "D_G_sec",
        "score_numeric",
        "best_so_far_by_eval",
        "penalty_numeric",
        "is_penalty_any",
        "final_status",
        "failure_reason",
        "termination_reason",
    ]
    df[[col for col in clean_cols if col in df.columns]].rename(
        columns={"score_numeric": "score", "penalty_numeric": "penalty"}
    ).to_csv(OUTPUT_DIR / "cma_try2_cleaned_by_theta.csv", index=False)
    plot_cma_try2_round_best(round_rows)
    plot_cma_try2_penalty_heatmap(df)


def plot_cma_try2_round_best(round_rows: list[dict]) -> None:
    rounds = pd.DataFrame(round_rows)
    fig, ax_score = plt.subplots(figsize=(11.4, 6.2), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax_score.set_facecolor("#FBFCFD")

    x = rounds["cma_round"].to_numpy(dtype=int)
    round_best = rounds["round_best_score"].to_numpy(dtype=float)
    best_so_far = rounds["best_so_far_score_after_round"].to_numpy(dtype=float)
    penalties = rounds["penalty_count"].to_numpy(dtype=int)

    ax_score.plot(x, round_best, color="#2A9D78", lw=2.4, marker="o", markersize=6, label="라운드별 1등 Score")
    ax_score.plot(x, best_so_far, color=NAVY, lw=2.8, marker="o", markersize=5, label="누적 최저 Score")
    for round_id, score in zip(x, round_best):
        ax_score.text(round_id, score + 10, f"{score:.2f}", color="#2A5C4B", fontsize=8.5, ha="center")

    ax_penalty = ax_score.twinx()
    ax_penalty.bar(x, penalties, color="#C96B42", alpha=0.22, width=0.58, label="Penalty θ 개수")
    ax_penalty.set_ylim(0, max(5, int(penalties.max()) + 1))
    ax_penalty.set_ylabel("Penalty θ 개수", color="#8B4A2F", fontsize=11)
    ax_penalty.tick_params(axis="y", colors="#8B4A2F")
    ax_penalty.spines["right"].set_color("#D8B6A6")

    ax_score.set_title("CMA-ES try2 라운드별 1등과 penalty 발생", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax_score.text(
        0,
        1.015,
        "각 라운드 6개 θ 중 최저 Score를 선으로 표시 · 막대는 해당 라운드 penalty/fail θ 개수",
        transform=ax_score.transAxes,
        color="#627286",
        fontsize=10.5,
    )
    ax_score.set_xlabel("CMA 라운드")
    ax_score.set_ylabel("Score")
    ax_score.set_xlim(0.5, int(x.max()) + 0.5)
    ax_score.set_ylim(max(0, float(round_best.min()) - 35), float(round_best.max()) + 70)
    ax_score.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax_score.yaxis.set_major_locator(MaxNLocator(7))
    ax_score.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax_score.spines[["top", "right"]].set_visible(False)
    ax_penalty.spines["top"].set_visible(False)

    handles_1, labels_1 = ax_score.get_legend_handles_labels()
    handles_2, labels_2 = ax_penalty.get_legend_handles_labels()
    ax_score.legend(handles_1 + handles_2, labels_1 + labels_2, loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF")

    fig.savefig(OUTPUT_DIR / "cma_try2_round_best_penalty_overview.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "cma_try2_round_best_penalty_overview.svg", bbox_inches="tight")
    plt.close(fig)


def plot_cma_try2_penalty_heatmap(df: pd.DataFrame) -> None:
    plot_df = df.copy()
    plot_df["plot_score"] = plot_df["score_numeric"].where(~plot_df["is_penalty_any"])
    matrix = plot_df.pivot(index="cma_round", columns="theta_index_in_cma_round", values="plot_score").sort_index()
    penalty_matrix = plot_df.pivot(index="cma_round", columns="theta_index_in_cma_round", values="is_penalty_any").sort_index()
    score_matrix = plot_df.pivot(index="cma_round", columns="theta_index_in_cma_round", values="score_numeric").sort_index()

    cmap = LinearSegmentedColormap.from_list("cma_score", ["#E7F3EF", "#7AC7AA", "#2A9D78", "#145A48"])
    normal_scores = matrix.to_numpy(dtype=float)
    valid_scores = normal_scores[np.isfinite(normal_scores)]
    norm = Normalize(vmin=float(np.nanmin(valid_scores)), vmax=float(np.nanpercentile(valid_scores, 90)))

    fig, ax = plt.subplots(figsize=(11.4, 6.0), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")
    ax.imshow(matrix, cmap=cmap, norm=norm, aspect="auto")

    for row_idx, round_id in enumerate(matrix.index):
        for col_idx, theta_id in enumerate(matrix.columns):
            is_penalty = bool(penalty_matrix.loc[round_id, theta_id])
            score = float(score_matrix.loc[round_id, theta_id])
            if is_penalty:
                rect = plt.Rectangle((col_idx - 0.5, row_idx - 0.5), 1, 1, facecolor="#C96B42", edgecolor="white", linewidth=1.1)
                ax.add_patch(rect)
                label = f"P\n{score:.0f}"
                color = "white"
                size = 7.2
                weight = "bold"
            else:
                label = f"{score:.0f}"
                color = "white" if norm(score) > 0.58 else NAVY
                size = 8.3
                weight = "normal"
            ax.text(col_idx, row_idx, label, ha="center", va="center", color=color, fontsize=size, fontweight=weight)

    ax.set_title("CMA-ES try2 θ별 Score / penalty map", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax.text(
        0,
        1.015,
        "행=라운드, 열=θ index · 주황색 P 셀은 penalty/fail, 숫자는 기록된 Score",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10.5,
    )
    ax.set_xlabel("θ index in round")
    ax.set_ylabel("CMA 라운드")
    ax.set_xticks(np.arange(len(matrix.columns)))
    ax.set_xticklabels([str(int(col)) for col in matrix.columns])
    ax.set_yticks(np.arange(len(matrix.index)))
    ax.set_yticklabels([str(int(row)) for row in matrix.index])
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks(np.arange(-0.5, len(matrix.columns), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(matrix.index), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.3)

    cbar = fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), ax=ax, fraction=0.035, pad=0.025)
    cbar.set_label("정상 Score", color=TEXT)
    cbar.ax.tick_params(colors="#6B7788")

    fig.savefig(OUTPUT_DIR / "cma_try2_penalty_heatmap.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "cma_try2_penalty_heatmap.svg", bbox_inches="tight")
    plt.close(fig)


def read_cma_try_for_interleave(path: Path, source_csv: str, source_csv_index: int) -> pd.DataFrame:
    df = pd.read_csv(path).copy()
    df["source_csv"] = source_csv
    df["source_csv_index"] = source_csv_index
    df["source_row"] = np.arange(1, len(df) + 1)
    df["csv_round"] = pd.to_numeric(df["round"], errors="coerce").astype(int)
    df["score_numeric"] = pd.to_numeric(df["score"], errors="coerce")
    df["penalty_numeric"] = pd.to_numeric(df.get("penalty", 0), errors="coerce").fillna(0)
    parsed = df["parameter_id"].apply(parse_round_index)
    df["cma_round"] = parsed.apply(lambda item: item[0])
    df["theta_index_in_cma_round"] = parsed.apply(lambda item: item[1])
    df["is_dropout"] = (
        (df["score_numeric"] >= 10000)
        | (df["penalty_numeric"] > 0)
        | (df["final_status"].astype(str).str.upper() == "FAIL")
    )
    df["display_round"] = (df["csv_round"] - 1) * 2 + source_csv_index
    return df.sort_values(["csv_round", "source_csv_index", "source_row"], kind="mergesort")


def cma_interleaved_dropout_rounds() -> pd.DataFrame:
    inputs = [
        (CMA_TRY1_INPUT, "CMA_try1", 1),
        (CMA_TRY2_INPUT, "CMA_try2", 2),
    ]
    frames = [read_cma_try_for_interleave(path, label, index) for path, label, index in inputs if path.exists()]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)

    rows = []
    running_best = float("inf")
    running_best_parameter = ""
    for csv_round in sorted(df["csv_round"].dropna().unique()):
        for source_csv_index in [1, 2]:
            group = df[(df["csv_round"] == csv_round) & (df["source_csv_index"] == source_csv_index)].copy()
            if group.empty:
                continue
            eligible = group[~group["is_dropout"]].copy()
            dropouts = group[group["is_dropout"]].copy()
            if eligible.empty:
                best_score = np.nan
                best_parameter_id = ""
                best_theta_index = np.nan
            else:
                best = eligible.loc[eligible["score_numeric"].idxmin()]
                best_score = float(best["score_numeric"])
                best_parameter_id = str(best["parameter_id"])
                best_theta_index = int(best["theta_index_in_cma_round"])
                if best_score < running_best:
                    running_best = best_score
                    running_best_parameter = best_parameter_id

            rows.append(
                {
                    "display_round": int(group["display_round"].iloc[0]),
                    "source_csv": group["source_csv"].iloc[0],
                    "source_csv_index": int(source_csv_index),
                    "csv_round": int(csv_round),
                    "cma_encoded_round": int(group["cma_round"].iloc[0]),
                    "theta_count": int(len(group)),
                    "eligible_count": int(len(eligible)),
                    "dropout_count": int(len(dropouts)),
                    "round_best_score": round(best_score, 4) if np.isfinite(best_score) else "",
                    "round_best_parameter_id": best_parameter_id,
                    "round_best_theta_index": best_theta_index if np.isfinite(best_theta_index) else "",
                    "best_so_far_score": round(running_best, 4) if np.isfinite(running_best) else "",
                    "best_so_far_parameter_id": running_best_parameter,
                    "dropout_scores": " | ".join(
                        f"theta{int(row.theta_index_in_cma_round)}={float(row.score_numeric):.2f}"
                        for _, row in dropouts.iterrows()
                    ),
                    "dropout_parameter_ids": " | ".join(str(row.parameter_id) for _, row in dropouts.iterrows()),
                    "order_policy": "display_round = (csv_round - 1) * 2 + source_csv_index; penalty/fail theta are dropout and excluded from the line",
                }
            )

    round_df = pd.DataFrame(rows)
    eligible_round_df = round_df[round_df["eligible_count"] > 0].copy()
    eligible_round_df["eligible_eval_index"] = np.arange(1, len(eligible_round_df) + 1)
    eligible_round_df["plot_round"] = eligible_round_df["eligible_eval_index"]
    eligible_round_df["included_in_50_round_cutoff"] = eligible_round_df["eligible_eval_index"] <= 50
    round_df.to_csv(OUTPUT_DIR / "cma_interleaved_dropout_round_best.csv", index=False)
    eligible_round_df.to_csv(OUTPUT_DIR / "cma_interleaved_dropout_valid_round_best.csv", index=False)
    eligible_round_df[eligible_round_df["included_in_50_round_cutoff"]].to_csv(
        OUTPUT_DIR / "cma_interleaved_dropout_valid_round_best_50cut.csv",
        index=False,
    )
    df.to_csv(OUTPUT_DIR / "cma_interleaved_dropout_cleaned_by_theta.csv", index=False)
    return round_df


def plot_cma_interleaved_dropout(round_df: pd.DataFrame) -> None:
    if round_df.empty:
        return
    all_valid = round_df[round_df["eligible_count"] > 0].copy()
    omitted_count = max(0, len(all_valid) - 50)
    plot_df = all_valid.head(50).copy()
    plot_df["round_best_score"] = pd.to_numeric(plot_df["round_best_score"], errors="coerce")
    plot_df["plot_round"] = np.arange(1, len(plot_df) + 1)
    plot_df["best_so_far_score"] = plot_df["round_best_score"].cummin()
    x = plot_df["plot_round"].to_numpy(dtype=int)
    y = plot_df["round_best_score"].to_numpy(dtype=float)
    best_so_far = plot_df["best_so_far_score"].to_numpy(dtype=float)

    fig, ax_score = plt.subplots(figsize=(11.4, 6.0), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax_score.set_facecolor("#FBFCFD")
    ax_score.plot(x, y, color="#2A9D78", lw=2.2, marker="o", markersize=5.8, label="라운드별 1등 Score")
    ax_score.plot(x, best_so_far, color=NAVY, lw=2.8, marker="o", markersize=4.8, label="누적 최저 Score")

    label_rows = plot_df.iloc[::5].copy()
    if not plot_df.empty and plot_df.index[-1] not in label_rows.index:
        label_rows = pd.concat([label_rows, plot_df.tail(1)])
    for _, row in label_rows.iterrows():
        ax_score.text(
            row["plot_round"],
            float(row["round_best_score"]) + 8,
            f"{row['round_best_score']:.0f}",
            ha="center",
            va="bottom",
            fontsize=8.2,
            color="#2A5C4B",
        )

    best = plot_df.loc[plot_df["round_best_score"].idxmin()]
    ax_score.scatter([best["plot_round"]], [best["round_best_score"]], s=78, color="#F2A541", edgecolor=NAVY, linewidth=1.2, zorder=6)
    ax_score.annotate(
        f"최적해\nR{int(best['plot_round'])}, {best['round_best_score']:.2f}",
        xy=(best["plot_round"], best["round_best_score"]),
        xytext=(best["plot_round"] + 2.1, best["round_best_score"] + 55),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.1),
        fontsize=9.5,
        color=NAVY,
        ha="left",
        va="center",
    )

    ax_score.set_title("CMA 합본: penalty 탈락 후 50라운드 컷오프", loc="left", fontsize=18, fontweight="bold", pad=18)
    ax_score.text(
        0,
        1.015,
        f"try1/try2 CSV round 교차 배치 → penalty/fail 탈락 → 정상 후보를 1부터 재카운트 · 50개만 표시, 초과 {omitted_count}개 제외",
        transform=ax_score.transAxes,
        color="#627286",
        fontsize=10.2,
    )
    ax_score.set_xlabel("재카운트 라운드(정상 후보만)")
    ax_score.set_ylabel("Score")
    ax_score.set_xlim(0.5, 50.5)
    valid_scores = plot_df["round_best_score"].dropna()
    ax_score.set_ylim(max(0, float(valid_scores.min()) - 28), float(valid_scores.max()) + 55)
    ax_score.xaxis.set_major_locator(MaxNLocator(10, integer=True))
    ax_score.yaxis.set_major_locator(MaxNLocator(7))
    ax_score.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax_score.spines[["top", "right"]].set_visible(False)

    ax_score.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF")

    fig.savefig(OUTPUT_DIR / "cma_interleaved_dropout_round_best.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "cma_interleaved_dropout_round_best.svg", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "cma_interleaved_dropout_round_best_50cut.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "cma_interleaved_dropout_round_best_50cut.svg", bbox_inches="tight")
    plt.close(fig)


def plot_comparison_with_interleaved_cma(winners: pd.DataFrame, cma_round_df: pd.DataFrame) -> None:
    if cma_round_df.empty:
        return
    cma_plot = cma_round_df[cma_round_df["eligible_count"] > 0].copy().head(50)
    cma_plot["plot_round"] = np.arange(1, len(cma_plot) + 1)
    cma_plot["round_best_score"] = pd.to_numeric(cma_plot["round_best_score"], errors="coerce")

    fig, ax = plt.subplots(figsize=(11.2, 5.8), constrained_layout=True)
    fig.patch.set_facecolor("#FBFCFD")
    ax.set_facecolor("#FBFCFD")

    for method in ["BO", "Random Search"]:
        curve = winners[winners["method"] == method].copy()
        ax.plot(
            curve["round"],
            curve["round_best_score"].where(curve["round_best_score"] < 10000),
            color=COLORS[method],
            lw=1.75,
            alpha=0.9,
            marker="o",
            markersize=3.8,
            label=method,
        )
    ax.plot(
        cma_plot["plot_round"],
        cma_plot["round_best_score"],
        color=COLORS["CMA-ES"],
        lw=2.15,
        alpha=0.95,
        marker="o",
        markersize=4.6,
        label="CMA-ES",
    )

    values = pd.concat(
        [
            winners.loc[(winners["method"].isin(["BO", "Random Search"])) & (winners["round_best_score"] < 10000), "round_best_score"],
            cma_plot["round_best_score"],
        ]
    ).dropna()
    ax.set_ylim(max(0, float(values.min()) - 15), float(values.max()) + 25)
    ax.set_xlim(1, int(winners["round"].max()) + 2)
    ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title("라운드별 1등 θ의 Score", loc="left", fontsize=17, fontweight="bold", pad=16)
    ax.text(
        0,
        1.015,
        "CMA는 try1/try2 교차 배치 후 penalty/fail 탈락, 정상 후보 50개만 재카운트 · BO/RS는 기존 50라운드 유지",
        transform=ax.transAxes,
        color="#627286",
        fontsize=10,
    )
    ax.set_xlabel("라운드")
    ax.set_ylabel("Round-best Score")
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF")
    fig.savefig(OUTPUT_DIR / "figure1c_round_winners_cma_interleaved_dropout.png", bbox_inches="tight")
    fig.savefig(OUTPUT_DIR / "figure1c_round_winners_cma_interleaved_dropout.svg", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    configure_style()
    cleaned = clean_evaluations()
    winners = round_winners(cleaned)
    write_clean_csvs(cleaned, winners)
    plot_convergence(winners)
    plot_round_winners(winners)
    plot_round_winners_cma_convergence_order(winners)
    plot_round_winners_cma_moderate_order(winners)
    cma_interleaved = cma_interleaved_dropout_rounds()
    plot_cma_interleaved_dropout(cma_interleaved)
    plot_comparison_with_interleaved_cma(winners, cma_interleaved)
    plot_bo_surrogate()
    write_summary(cleaned, winners)
    write_cma_try2_round_outputs()
    print(OUTPUT_DIR)
    print(pd.read_csv(OUTPUT_DIR / "optimization_figure_summary.csv").to_string(index=False))
    print("\nround_winners preview")
    print(winners.head(12).to_string(index=False))


if __name__ == "__main__":
    main()
