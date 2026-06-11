#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "gcpcsv" / "final result"
ALL_EVALUATIONS = INPUT_DIR / "gcp_weight_sensitivity_normalgp_parallel_m50_t4_all_evaluations.csv"
FINAL_RESULTS = INPUT_DIR / "gcp_weight_sensitivity_normalgp_parallel_m50_t4_final_sensitivity_results.csv"
PARETO_TABLE = INPUT_DIR / "gcp_weight_sensitivity_normalgp_parallel_m50_t4_table3_pareto.csv"
SPC_TABLE = INPUT_DIR / "gcp_weight_sensitivity_normalgp_parallel_m50_t4_table4_sensitivity_spc.csv"
OUTPUT_DIR = PROJECT_ROOT / "results" / "figures" / "gcp_weight_sensitivity_normalgp_parallel_m50_t4"

NAVY = "#0B1F3A"
BLUE = "#2F80C5"
BLUE_DARK = "#1F66B5"
BLUE_LIGHT = "#D8EAF8"
ORANGE = "#E65F2E"
AMBER = "#F2A541"
GREEN = "#2A9D78"
GRAY = "#A9B5C3"
GRAY_DARK = "#647386"
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
            "legend.fontsize": 8.7,
        }
    )


def ratio_order(value: str) -> float:
    left, right = str(value).split(":")
    return float(left) / max(float(right), 1.0e-9)


def short_id(parameter_id: str) -> str:
    parts = str(parameter_id).split("_")
    if len(parts) >= 3 and parts[0] == "bo":
        return f"{parts[1]}_{parts[2]}"
    return str(parameter_id)[:12]


def normalize_final(final: pd.DataFrame) -> pd.DataFrame:
    out = final.rename(
        columns={
            "input_parameter_id": "parameter_id",
            "input_t_lead": "t_lead",
            "input_delta_T_thr": "delta_T_thr",
            "input_G_ext": "G_ext",
            "input_Q_ratio": "Q_ratio",
            "input_tau": "tau",
            "output_D_E_sec": "D_E_sec",
            "output_D_G_sec": "D_G_sec",
        }
    ).copy()
    return out


def numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    all_eval = numeric(
        pd.read_csv(ALL_EVALUATIONS),
        [
            "round",
            "round_theta_index",
            "t_lead",
            "delta_T_thr",
            "G_ext",
            "Q_ratio",
            "tau",
            "D_E_sec",
            "D_G_sec",
            "score",
            "best_so_far",
            "penalty",
        ],
    )
    final = numeric(
        normalize_final(pd.read_csv(FINAL_RESULTS)),
        [
            "input_round",
            "t_lead",
            "delta_T_thr",
            "G_ext",
            "Q_ratio",
            "tau",
            "D_E_sec",
            "D_G_sec",
            "weight_E",
            "weight_G",
            "score",
            "stage2_on_count",
            "stage3_on_count",
        ],
    )
    pareto = numeric(
        pd.read_csv(PARETO_TABLE),
        [
            "t_lead",
            "delta_T_thr",
            "G_ext",
            "Q_ratio",
            "tau",
            "D_E_sec",
            "D_G_sec",
            "score",
            "rounds_completed",
        ],
    )
    spc = pd.read_csv(SPC_TABLE) if SPC_TABLE.exists() else pd.DataFrame()

    valid = all_eval[
        all_eval["D_E_sec"].notna()
        & all_eval["D_G_sec"].notna()
        & all_eval["score"].notna()
        & all_eval["score"].lt(10000)
        & all_eval["penalty"].fillna(0).eq(0)
        & all_eval["final_status"].fillna("PASS").astype(str).str.upper().eq("PASS")
    ].copy()
    for frame in [valid, final, pareto]:
        frame["ratio_order"] = frame["weight_ratio"].map(ratio_order)
        frame["short_id"] = frame["parameter_id"].map(short_id)

    final = final.sort_values("ratio_order").reset_index(drop=True)
    final["selected_by_csv"] = True
    final["global_best"] = final["score"].eq(final["score"].min())

    pareto = pareto.sort_values("ratio_order").reset_index(drop=True)
    pareto["selected_by_csv"] = True
    pareto["global_best"] = pareto["score"].eq(pareto["score"].min())
    return valid, final, pareto, spc


def add_representatives(valid: pd.DataFrame, final: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ratio, group in valid.groupby("weight_ratio", sort=False):
        group = group.sort_values("score", kind="mergesort")
        best = group.iloc[0].copy()
        csv_point = final[final["weight_ratio"].eq(ratio)]
        if csv_point.empty:
            best["representative_role"] = "Best"
            rows.append(best)
            continue
        csv_pid = str(csv_point.iloc[0]["parameter_id"])
        visually_distinct = group[
            ~(
                group["D_E_sec"].round(2).eq(float(csv_point.iloc[0]["D_E_sec"]))
                & group["D_G_sec"].round(2).eq(float(csv_point.iloc[0]["D_G_sec"]))
            )
        ].head(1)
        best["representative_role"] = "CSV best"
        rows.append(best)
        if not visually_distinct.empty and str(ratio) == "5:1":
            near = visually_distinct.iloc[0].copy()
            near["representative_role"] = "Near-tie display"
            near["score_gap_vs_csv"] = float(near["score"] - csv_point.iloc[0]["score"])
            near["csv_parameter_id"] = csv_pid
            rows.append(near)
    reps = pd.DataFrame(rows).sort_values(["ratio_order", "representative_role"]).reset_index(drop=True)
    reps["short_id"] = reps["parameter_id"].map(short_id)
    return reps


def axis_clean(ax: plt.Axes, grid_axis: str = "both") -> None:
    ax.set_facecolor(BACKGROUND)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    if grid_axis in {"both", "x"}:
        ax.grid(True, axis="x", color=GRID, linewidth=0.85, alpha=0.9)
    if grid_axis in {"both", "y"}:
        ax.grid(True, axis="y", color=GRID, linewidth=0.85, alpha=0.9)


def score_color_scale(values: pd.Series) -> np.ndarray:
    q_low, q_high = values.quantile([0.05, 0.95])
    span = max(float(q_high - q_low), 1.0e-9)
    scaled = 1.0 - np.clip((values - q_low) / span, 0, 1)
    return scaled.to_numpy(dtype=float)


def plot_tradeoff_landscape(valid: pd.DataFrame, final: pd.DataFrame, reps: pd.DataFrame, output_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(11.4, 7.0), constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    fig.subplots_adjust(left=0.105, right=0.965, top=0.82, bottom=0.13)
    axis_clean(ax, "both")

    colors = score_color_scale(valid["score"])
    scatter = ax.scatter(
        valid["D_G_sec"],
        valid["D_E_sec"],
        c=colors,
        cmap="Blues",
        s=24,
        alpha=0.28,
        linewidth=0,
        zorder=1,
    )

    final_unique = final.drop_duplicates(["D_E_sec", "D_G_sec", "parameter_id"]).copy()
    ax.plot(
        final.sort_values("ratio_order")["D_G_sec"],
        final.sort_values("ratio_order")["D_E_sec"],
        color=BLUE_DARK,
        lw=2.2,
        alpha=0.75,
        zorder=3,
    )
    ax.scatter(
        final_unique["D_G_sec"],
        final_unique["D_E_sec"],
        s=150,
        color=BLUE,
        edgecolor=NAVY,
        linewidth=1.25,
        zorder=4,
        label="CSV-selected optimum",
    )

    near = reps[reps["representative_role"].eq("Near-tie display")]
    if not near.empty:
        ax.scatter(
            near["D_G_sec"],
            near["D_E_sec"],
            s=260,
            facecolor="none",
            edgecolor=AMBER,
            linewidth=3.0,
            zorder=5,
            label="Near-tie display candidate",
        )

    grouped_labels = (
        final.groupby(["D_E_sec", "D_G_sec", "parameter_id"], as_index=False)
        .agg(weight_label=("weight_ratio", lambda values: ", ".join(values.astype(str))), score=("score", "min"))
        .sort_values("D_G_sec")
    )
    label_offsets = {
        "bo_r09_002_tl0_dt0_ge19_qr62_tau83": (1.0, 8.0),
        "bo_r24_002_tl0_dt0_ge17_qr33_tau84": (1.8, -7.8),
    }
    for _, row in grouped_labels.iterrows():
        dx, dy = label_offsets.get(str(row["parameter_id"]), (3, 3))
        ax.text(
            row["D_G_sec"] + dx,
            row["D_E_sec"] + dy,
            row["weight_label"],
            color=NAVY,
            fontsize=9.2,
            fontweight="bold",
            zorder=6,
        )
    if not near.empty:
        row = near.iloc[0]
        ax.annotate(
            f"near-tie for 5:1\nscore gap +{row['score_gap_vs_csv']:.2f}",
            xy=(row["D_G_sec"], row["D_E_sec"]),
            xytext=(row["D_G_sec"] + 5.2, row["D_E_sec"] + 31.5),
            arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.1),
            color=NAVY,
            fontsize=9.0,
            ha="left",
        )

    ax.set_title("Weight Sensitivity Trade-off Landscape", loc="left", fontsize=16.5, fontweight="bold", pad=18)
    ax.text(
        0.0,
        1.02,
        "Background points are valid BO evaluations; highlighted points are final CSV selections and a near-tie display candidate.",
        transform=ax.transAxes,
        color=MUTED,
        fontsize=9.4,
    )
    ax.set_xlabel("D_G General Delay (s)", fontsize=10.8)
    ax.set_ylabel("D_E Emergency Delay (s)", fontsize=10.8)
    ax.xaxis.set_major_locator(MaxNLocator(7))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    x_min = min(float(final["D_G_sec"].min()), float(near["D_G_sec"].min()) if not near.empty else np.inf) - 5.0
    x_max = max(float(valid["D_G_sec"].quantile(0.99)), float(final["D_G_sec"].max()) + 8.0)
    y_min = min(float(final["D_E_sec"].min()), float(near["D_E_sec"].min()) if not near.empty else np.inf) - 12.0
    y_max = max(float(valid["D_E_sec"].quantile(0.95)), float(final["D_E_sec"].max()) + 48.0)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)

    cbar = fig.colorbar(scatter, ax=ax, fraction=0.028, pad=0.018)
    cbar.set_label("Relative score quality", color=MUTED, fontsize=8.8)
    cbar.set_ticks([])
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)

    fig.savefig(output_dir / "gcp_weight_sensitivity_tradeoff_landscape.png", bbox_inches="tight")
    fig.savefig(output_dir / "gcp_weight_sensitivity_tradeoff_landscape.svg", bbox_inches="tight")
    plt.close(fig)


def plot_weight_response(final: pd.DataFrame, reps: pd.DataFrame, output_dir: Path) -> None:
    ordered = final.sort_values("ratio_order").reset_index(drop=True)
    x = np.arange(len(ordered))
    fig, ax1 = plt.subplots(figsize=(11.0, 5.7), constrained_layout=True)
    fig.patch.set_facecolor(BACKGROUND)
    axis_clean(ax1, "y")
    ax2 = ax1.twinx()
    ax2.spines[["top", "right"]].set_color("#D3DAE2")
    ax2.tick_params(colors=MUTED)

    bars = ax1.bar(x, ordered["score"], width=0.52, color=BLUE_LIGHT, edgecolor="white", linewidth=1.0, label="Weighted score")
    ax1.plot(x, ordered["D_E_sec"], color=ORANGE, lw=2.3, marker="o", label="D_E Emergency Delay")
    ax2.plot(x, ordered["D_G_sec"], color=BLUE_DARK, lw=2.3, marker="o", label="D_G General Delay")

    for idx, row in ordered.iterrows():
        ax1.text(idx, row["score"] + 3.0, f"{row['score']:.1f}", ha="center", va="bottom", fontsize=8.4, color=NAVY)
        ax1.text(idx, ax1.get_ylim()[0] + 4.0, row["short_id"], ha="center", va="bottom", fontsize=7.7, color=GRAY_DARK)

    near = reps[reps["representative_role"].eq("Near-tie display")]
    if not near.empty:
        gap = float(near.iloc[0]["score_gap_vs_csv"])
        ax1.annotate(
            f"5:1 has a near-tie alternative\n(+{gap:.2f} score) with clearer D_E-D_G trade-off",
            xy=(1, ordered.loc[1, "score"]),
            xytext=(1.75, ordered["score"].max() - 10),
            arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.05),
            fontsize=8.8,
            color=NAVY,
            ha="left",
        )

    ax1.set_xticks(x)
    ax1.set_xticklabels(ordered["weight_ratio"], fontsize=9.5)
    ax1.set_xlabel("Weight Ratio (D_E:D_G)", fontsize=10.5)
    ax1.set_ylabel("Weighted Score / D_E (s)", fontsize=10.2)
    ax2.set_ylabel("D_G General Delay (s)", fontsize=10.2, color=MUTED)
    ax1.set_title("Sensitivity Response by Weight Ratio", loc="left", fontsize=15.5, fontweight="bold", pad=14)
    ax1.text(
        0.0,
        1.02,
        "Higher D_E weight switches the selected optimum from r09_002 to r24_002 after 5:1.",
        transform=ax1.transAxes,
        color=MUTED,
        fontsize=9.2,
    )

    handles1, labels1 = ax1.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(handles1 + handles2, labels1 + labels2, loc="upper left", frameon=True, facecolor="white", edgecolor="#E1E7EF")

    fig.savefig(output_dir / "gcp_weight_sensitivity_response_by_ratio.png", bbox_inches="tight")
    fig.savefig(output_dir / "gcp_weight_sensitivity_response_by_ratio.svg", bbox_inches="tight")
    plt.close(fig)


def plot_convergence(valid: pd.DataFrame, output_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(11.0, 5.8), constrained_layout=True)
    fig.patch.set_facecolor(BACKGROUND)
    axis_clean(ax, "both")

    palette = {
        "1:1": "#1F66B5",
        "5:1": "#2A9D78",
        "10:1": "#E65F2E",
        "15:1": "#8F6AC8",
        "20:1": "#5B677A",
    }
    for ratio, group in valid.groupby("weight_ratio", sort=False):
        group = group.sort_values(["round", "round_theta_index"], kind="mergesort")
        per_round = group.groupby("round", as_index=False)["score"].min().sort_values("round")
        per_round["best_so_far"] = per_round["score"].cummin()
        ax.step(
            per_round["round"],
            per_round["best_so_far"],
            where="post",
            lw=2.1,
            color=palette.get(str(ratio), GRAY_DARK),
            label=str(ratio),
        )

    ax.set_title("BO Convergence by Weight Ratio", loc="left", fontsize=15.5, fontweight="bold", pad=14)
    ax.text(
        0.0,
        1.02,
        "Failed and penalty rows are excluded; curves show best-so-far valid score.",
        transform=ax.transAxes,
        color=MUTED,
        fontsize=9.2,
    )
    ax.set_xlabel("BO Round", fontsize=10.5)
    ax.set_ylabel("Best-so-far Score", fontsize=10.5)
    ax.xaxis.set_major_locator(MaxNLocator(7, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))
    ax.legend(title="Weight Ratio", loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF")

    fig.savefig(output_dir / "gcp_weight_sensitivity_convergence.png", bbox_inches="tight")
    fig.savefig(output_dir / "gcp_weight_sensitivity_convergence.svg", bbox_inches="tight")
    plt.close(fig)


def build(args: argparse.Namespace) -> None:
    configure_style()
    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    valid, final, pareto, spc = load_data()
    reps = add_representatives(valid, final)

    valid.to_csv(output_dir / "gcp_weight_sensitivity_valid_evaluations.csv", index=False)
    final.to_csv(output_dir / "gcp_weight_sensitivity_final_points_clean.csv", index=False)
    pareto.to_csv(output_dir / "gcp_weight_sensitivity_pareto_clean.csv", index=False)
    reps.to_csv(output_dir / "gcp_weight_sensitivity_display_representatives.csv", index=False)

    plot_tradeoff_landscape(valid, final, reps, output_dir)
    plot_weight_response(final, reps, output_dir)
    plot_convergence(valid, output_dir)

    print(output_dir)
    print("valid_rows", len(valid), "final_rows", len(final), "spc_rows", len(spc))
    print(final[["weight_ratio", "parameter_id", "D_E_sec", "D_G_sec", "score", "short_id"]].round(3).to_string(index=False))
    near = reps[reps["representative_role"].eq("Near-tie display")]
    if not near.empty:
        cols = ["weight_ratio", "parameter_id", "D_E_sec", "D_G_sec", "score", "score_gap_vs_csv"]
        print("near_tie")
        print(near[cols].round(3).to_string(index=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build GCP NormalGP weight sensitivity figures.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    return parser.parse_args()


def main() -> None:
    build(parse_args())


if __name__ == "__main__":
    main()
