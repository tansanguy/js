#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "gcpcsv" / "final result"
FINAL_RESULTS = INPUT_DIR / "local_weight_sensitivity_normalgp_BO_final_sensitivity_results.csv"
PARETO_TABLE = INPUT_DIR / "local_weight_sensitivity_normalgp_BO_table3_pareto.csv"
SPC_TABLE = INPUT_DIR / "local_weight_sensitivity_normalgp_BO_table4_sensitivity_spc.csv"
OUTPUT_DIR = PROJECT_ROOT / "results" / "figures" / "local_weight_sensitivity_normalgp_BO"

NAVY = "#0B1F3A"
BLUE = "#2F80C5"
BLUE_LIGHT = "#88BDEB"
ORANGE = "#E65F2E"
GREEN = "#2A9D78"
GRAY = "#7D8B9C"
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
    right_f = float(right)
    return float(left) / right_f if right_f else np.inf


def short_id(parameter_id: str) -> str:
    parts = str(parameter_id).split("_")
    if len(parts) >= 3 and parts[0] == "bo":
        return f"{parts[1]}_{parts[2]}"
    return str(parameter_id)[:12]


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    final = pd.read_csv(FINAL_RESULTS)
    pareto = pd.read_csv(PARETO_TABLE)
    spc = pd.read_csv(SPC_TABLE) if SPC_TABLE.exists() else pd.DataFrame()

    final = final.rename(
        columns={
            "output_D_E_sec": "D_E_sec",
            "output_D_G_sec": "D_G_sec",
        }
    )
    for frame in [final, pareto]:
        for col in [
            "D_E_sec",
            "D_G_sec",
            "score",
            "weight_E",
            "weight_G",
            "input_t_lead",
            "input_delta_T_thr",
            "input_G_ext",
            "input_Q_ratio",
            "input_tau",
            "t_lead",
            "delta_T_thr",
            "G_ext",
            "Q_ratio",
            "tau",
        ]:
            if col in frame.columns:
                frame[col] = pd.to_numeric(frame[col], errors="coerce")
    final["ratio_order"] = final["weight_ratio"].map(ratio_order)
    final["short_id"] = final["input_parameter_id"].map(short_id)
    final["is_global_best"] = final["score"].eq(final["score"].min())

    if "is_knee" in pareto.columns:
        knee_ids = set(pareto.loc[pareto["is_knee"].astype(str).str.lower().eq("true"), "weight_ratio"].astype(str))
    else:
        knee_ids = set()
    final["csv_knee_flag"] = final["weight_ratio"].astype(str).isin(knee_ids)

    cluster = final[~final["is_global_best"]].copy()
    if cluster.empty:
        display_ratio = str(final.loc[final["score"].idxmin(), "weight_ratio"])
    else:
        display_ratio = str(cluster.loc[cluster["score"].idxmin(), "weight_ratio"])
    final["display_anchor"] = final["weight_ratio"].astype(str).eq(display_ratio)
    return final.sort_values("ratio_order").reset_index(drop=True), pareto, spc


def axis_clean(ax: plt.Axes, grid_axis: str = "both") -> None:
    ax.set_facecolor(BACKGROUND)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    if grid_axis in {"both", "x"}:
        ax.grid(True, axis="x", color=GRID, linewidth=0.85, alpha=0.9)
    if grid_axis in {"both", "y"}:
        ax.grid(True, axis="y", color=GRID, linewidth=0.85, alpha=0.9)


def smooth_path(points: pd.DataFrame, samples: int = 120) -> tuple[np.ndarray, np.ndarray]:
    ordered = points.sort_values("ratio_order")
    t = np.arange(len(ordered), dtype=float)
    x = ordered["D_G_sec"].to_numpy(dtype=float)
    y = ordered["D_E_sec"].to_numpy(dtype=float)
    if len(ordered) < 3:
        grid = np.linspace(t.min(), t.max(), samples)
        return np.interp(grid, t, x), np.interp(grid, t, y)
    try:
        from scipy.interpolate import make_interp_spline  # type: ignore

        degree = min(3, len(ordered) - 1)
        grid = np.linspace(t.min(), t.max(), samples)
        return make_interp_spline(t, x, k=degree)(grid), make_interp_spline(t, y, k=degree)(grid)
    except Exception:
        grid = np.linspace(t.min(), t.max(), samples)
        return np.interp(grid, t, x), np.interp(grid, t, y)


def point_style(row: pd.Series, zoom: bool = False) -> tuple[str, str, float, float]:
    if bool(row["is_global_best"]):
        return GREEN, NAVY, 150 if zoom else 170, 1.45
    if bool(row["display_anchor"]):
        return ORANGE, "white", 180 if zoom else 190, 1.55
    return BLUE, "white", 105 if zoom else 115, 1.15


def plot_reference_curve(df: pd.DataFrame, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(12.8, 6.7), constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.16, 1.0], left=0.075, right=0.985, top=0.78, bottom=0.14, wspace=0.23)
    ax_main = fig.add_subplot(gs[0, 0])
    ax_zoom = fig.add_subplot(gs[0, 1])

    fig.text(0.075, 0.925, "Weight Sensitivity: D_G-D_E Trade-off", fontsize=17.8, fontweight="bold", color=NAVY, ha="left")
    fig.text(
        0.075,
        0.875,
        "Local CSV-only Normal GP + BO results; points are exact CSV outputs, curve is a visual guide.",
        fontsize=9.8,
        color=MUTED,
        ha="left",
    )

    for ax in [ax_main, ax_zoom]:
        axis_clean(ax, "both")
        xs, ys = smooth_path(df)
        ax.plot(xs, ys, color=BLUE_LIGHT, lw=2.8, alpha=0.72, zorder=1, solid_capstyle="round")
        for _, row in df.iterrows():
            face, edge, size, width = point_style(row, zoom=ax is ax_zoom)
            ax.scatter(row["D_G_sec"], row["D_E_sec"], s=size, color=face, edgecolor=edge, linewidth=width, zorder=4)
            if bool(row["csv_knee_flag"]):
                ax.scatter(row["D_G_sec"], row["D_E_sec"], s=size + 90, facecolor="none", edgecolor=NAVY, linewidth=1.35, zorder=5)
        ax.set_xlabel("D_G General Delay (s)", fontsize=10.6)
        ax.set_ylabel("D_E Emergency Delay (s)", fontsize=10.6)
        ax.xaxis.set_major_locator(MaxNLocator(6))
        ax.yaxis.set_major_locator(MaxNLocator(6))

    ax_main.set_title("Overview", loc="left", fontsize=12.4, fontweight="bold", pad=10)
    xpad = max((df["D_G_sec"].max() - df["D_G_sec"].min()) * 0.08, 8)
    ypad = max((df["D_E_sec"].max() - df["D_E_sec"].min()) * 0.18, 8)
    ax_main.set_xlim(float(df["D_G_sec"].min()) - xpad, float(df["D_G_sec"].max()) + xpad)
    ax_main.set_ylim(float(df["D_E_sec"].min()) - ypad, float(df["D_E_sec"].max()) + ypad)

    label_offsets = {
        "1:1": (8.0, -5.0),
        "5:1": (-24.0, -6.2),
        "10:1": (-25.0, 3.2),
        "15:1": (4.0, 2.4),
        "20:1": (5.0, -2.8),
    }
    for _, row in df.iterrows():
        dx, dy = label_offsets.get(str(row["weight_ratio"]), (4.0, 2.0))
        ax_main.text(row["D_G_sec"] + dx, row["D_E_sec"] + dy, str(row["weight_ratio"]), color=NAVY, fontsize=9.2, fontweight="bold")

    cluster = df[~df["is_global_best"]].copy()
    ax_zoom.set_title("High-Weight Cluster Zoom", loc="left", fontsize=12.4, fontweight="bold", pad=10)
    ax_zoom.set_xlim(float(cluster["D_G_sec"].min()) - 8.0, float(cluster["D_G_sec"].max()) + 8.0)
    ax_zoom.set_ylim(float(cluster["D_E_sec"].min()) - 9.0, float(cluster["D_E_sec"].max()) + 9.0)
    zoom_offsets = {
        "5:1": (-3.7, 1.8),
        "10:1": (-7.7, 2.2),
        "15:1": (1.2, 2.1),
        "20:1": (1.3, -3.0),
    }
    for _, row in cluster.iterrows():
        dx, dy = zoom_offsets.get(str(row["weight_ratio"]), (1.0, 1.0))
        label = f"{row['weight_ratio']} anchor" if bool(row["display_anchor"]) else str(row["weight_ratio"])
        ax_zoom.text(row["D_G_sec"] + dx, row["D_E_sec"] + dy, label, color=NAVY, fontsize=9.1, fontweight="bold")

    knee = df[df["csv_knee_flag"]]
    if not knee.empty:
        row = knee.iloc[0]
        ax_zoom.annotate(
            f"CSV knee flag: {row['weight_ratio']}",
            xy=(row["D_G_sec"], row["D_E_sec"]),
            xytext=(row["D_G_sec"] - 7.0, row["D_E_sec"] + 5.2),
            arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.05),
            fontsize=8.6,
            color=NAVY,
            ha="right",
        )

    fig.savefig(output_dir / "normalgp_bo_sensitivity_dg_de_curve.png", bbox_inches="tight")
    fig.savefig(output_dir / "normalgp_bo_sensitivity_dg_de_curve.svg", bbox_inches="tight")
    plt.close(fig)


def plot_score_components(df: pd.DataFrame, output_dir: Path) -> None:
    ordered = df.sort_values("ratio_order").reset_index(drop=True)
    x = np.arange(len(ordered))
    fig, ax1 = plt.subplots(figsize=(10.8, 5.4), constrained_layout=True)
    fig.patch.set_facecolor(BACKGROUND)
    axis_clean(ax1, "y")
    ax2 = ax1.twinx()
    ax2.spines[["top", "right"]].set_color("#D3DAE2")
    ax2.tick_params(colors=MUTED)
    ax2.set_ylabel("D_G General Delay (s)", color=MUTED, fontsize=10.0)

    ax1.plot(x, ordered["D_E_sec"], marker="o", color=ORANGE, lw=2.2, label="D_E Emergency Delay")
    ax2.plot(x, ordered["D_G_sec"], marker="o", color=BLUE, lw=2.2, label="D_G General Delay")
    ax1.bar(x, ordered["score"], width=0.55, color="#CAD8E6", alpha=0.42, label="Score")

    ax1.set_xticks(x)
    ax1.set_xticklabels(ordered["weight_ratio"], fontsize=9.6)
    ax1.set_xlabel("Weight Ratio", fontsize=10.3)
    ax1.set_ylabel("D_E / Score", fontsize=10.0)
    ax1.set_title("Sensitivity by Weight Ratio", loc="left", fontsize=14.5, fontweight="bold", pad=12)
    ax1.text(0, 1.015, "Score bars share the left axis with D_E; D_G uses the right axis.", transform=ax1.transAxes, color=MUTED, fontsize=9.0)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", frameon=True, facecolor="white", edgecolor="#E1E7EF")
    fig.savefig(output_dir / "normalgp_bo_sensitivity_by_ratio.png", bbox_inches="tight")
    fig.savefig(output_dir / "normalgp_bo_sensitivity_by_ratio.svg", bbox_inches="tight")
    plt.close(fig)


def write_clean_tables(df: pd.DataFrame, pareto: pd.DataFrame, spc: pd.DataFrame, output_dir: Path) -> None:
    cols = [
        "weight_ratio",
        "input_parameter_id",
        "input_t_lead",
        "input_delta_T_thr",
        "input_G_ext",
        "input_Q_ratio",
        "input_tau",
        "D_E_sec",
        "D_G_sec",
        "score",
        "measured_D_E_sec",
        "measured_D_G_sec",
        "stage2_on_count",
        "stage3_on_count",
        "is_global_best",
        "display_anchor",
        "csv_knee_flag",
    ]
    df[[c for c in cols if c in df.columns]].to_csv(output_dir / "normalgp_bo_sensitivity_clean_points.csv", index=False)
    pareto.to_csv(output_dir / "normalgp_bo_sensitivity_pareto_source.csv", index=False)
    spc.to_csv(output_dir / "normalgp_bo_sensitivity_spc_source.csv", index=False)


def build(args: argparse.Namespace) -> None:
    configure_style()
    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    df, pareto, spc = load_data()
    write_clean_tables(df, pareto, spc, output_dir)
    plot_reference_curve(df, output_dir)
    plot_score_components(df, output_dir)
    print(output_dir / "normalgp_bo_sensitivity_dg_de_curve.png")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local Normal GP + BO weight sensitivity figures.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    return parser.parse_args()


def main() -> None:
    build(parse_args())


if __name__ == "__main__":
    main()
