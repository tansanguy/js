#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import warnings
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import Normalize
from matplotlib.ticker import MaxNLocator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_SCRIPT = Path(__file__).with_name("build_bo_gp_snapshot_visualization.py")
DEFAULT_BO_CSV = PROJECT_ROOT / "gcpcsv" / "final result" / "clean" / "BO_06092213.csv"
DEFAULT_SNAPSHOT_DIR = PROJECT_ROOT / "gcpcsv" / "final result" / "reference"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "figures" / "bo_gp_sensitivity_presentation_06092213"

THETA_FIELDS = ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]
LABELS = {
    "t_lead": "Lead Time (s)",
    "delta_T_thr": "ETA Gate Threshold (s)",
    "G_ext": "Green Extension (s)",
    "Q_ratio": "Queue Ratio",
    "tau": "Spillback Threshold",
}

NAVY = "#0B1F3A"
BLUE = "#1F66B5"
BLUE_LIGHT = "#D7E7F5"
GREEN = "#2A9D78"
GREEN_LIGHT = "#DCEFE8"
ORANGE = "#F2A541"
GRAY = "#8B98A8"
GRID = "#DDE5EE"
TEXT = "#25364A"
MUTED = "#6B7788"
BACKGROUND = "#FBFCFD"


def load_base_module() -> Any:
    spec = importlib.util.spec_from_file_location("bo_gp_base", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import base script: {BASE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
            "legend.fontsize": 9.0,
        }
    )


def resolve_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def prepare_axis(ax: plt.Axes, *, grid_axis: str = "y") -> None:
    ax.set_facecolor(BACKGROUND)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    if grid_axis in {"x", "both"}:
        ax.grid(True, axis="x", color=GRID, linewidth=0.85, alpha=0.9)
    if grid_axis in {"y", "both"}:
        ax.grid(True, axis="y", color=GRID, linewidth=0.85, alpha=0.9)
    ax.yaxis.set_major_locator(MaxNLocator(5))


def display_limits(observations: pd.DataFrame, slices: pd.DataFrame) -> tuple[float, float]:
    observed = observations["score"].to_numpy(dtype=float)
    gp_low = (slices["gp_mean"] - slices["gp_std"]).to_numpy(dtype=float)
    gp_high = (slices["gp_mean"] + slices["gp_std"]).to_numpy(dtype=float)
    values = np.concatenate([observed[np.isfinite(observed)], gp_low[np.isfinite(gp_low)], gp_high[np.isfinite(gp_high)]])
    low = float(np.nanpercentile(values, 1.0))
    high = float(np.nanpercentile(values, 98.0))
    pad = max((high - low) * 0.12, 8.0)
    return max(0.0, low - pad), high + pad


def build_sensitivity_summary(slices: pd.DataFrame, best: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for field in THETA_FIELDS:
        part = slices[slices["slice_parameter"].eq(field)].sort_values("slice_value")
        if part.empty:
            continue
        min_idx = part["gp_mean"].idxmin()
        min_row = part.loc[min_idx]
        effect_range = float(part["gp_mean"].max() - part["gp_mean"].min())
        near_window = max(5.0, effect_range * 0.15)
        near = part[part["gp_mean"].le(float(min_row["gp_mean"]) + near_window)]
        rows.append(
            {
                "parameter": field,
                "label": LABELS[field],
                "best_observed_value": float(best[field]),
                "predicted_best_value": float(min_row["slice_value"]),
                "predicted_min_score": float(min_row["gp_mean"]),
                "effect_range_score": effect_range,
                "mean_gp_std": float(part["gp_std"].mean()),
                "near_best_low": float(near["slice_value"].min()),
                "near_best_high": float(near["slice_value"].max()),
                "near_best_window_score": near_window,
            }
        )
    summary = pd.DataFrame(rows)
    return summary.sort_values("effect_range_score", ascending=False).reset_index(drop=True)


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for ext in ("png", "svg"):
        path = output_dir / f"{stem}.{ext}"
        fig.savefig(path, bbox_inches="tight")
        paths.append(path)
    plt.close(fig)
    return paths


def plot_sensitivity_ranking(summary: pd.DataFrame, output_dir: Path) -> list[Path]:
    data = summary.sort_values("effect_range_score", ascending=True)
    fig, ax = plt.subplots(figsize=(9.2, 4.7), constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    fig.subplots_adjust(left=0.28, right=0.96, top=0.78, bottom=0.16)
    prepare_axis(ax, grid_axis="x")

    y = np.arange(len(data))
    colors = [BLUE if i == len(data) - 1 else "#8FB7DF" for i in range(len(data))]
    ax.barh(y, data["effect_range_score"], height=0.58, color=colors, edgecolor="white", linewidth=0.8)
    for yi, value in zip(y, data["effect_range_score"], strict=False):
        ax.text(float(value) + 1.0, yi, f"{value:.1f}", va="center", ha="left", fontsize=9.4, color=TEXT)
    ax.set_yticks(y)
    ax.set_yticklabels(data["label"], fontsize=10.4)
    ax.set_xlabel("GP Mean Score Range", fontsize=10.8)
    ax.set_title("Parameter Sensitivity Ranking", loc="left", fontsize=16.0, fontweight="bold", pad=18)
    ax.text(
        0.0,
        1.03,
        "Higher bars indicate stronger one-dimensional influence in the refit GP surface.",
        transform=ax.transAxes,
        color=MUTED,
        fontsize=9.3,
    )
    ax.set_xlim(0, max(float(data["effect_range_score"].max()) * 1.18, 1.0))
    return save_figure(fig, output_dir, "gp_parameter_sensitivity_ranking")


def plot_best_parameter_summary(best: dict[str, Any], output_dir: Path) -> list[Path]:
    labels = [LABELS[field] for field in THETA_FIELDS] + ["Score"]
    values = [float(best[field]) for field in THETA_FIELDS] + [float(best["score"])]
    display = [f"{v:.0f}" if name not in {"Queue Ratio", "Spillback Threshold"} else f"{v:.2f}" for name, v in zip(labels, values, strict=False)]
    display[-1] = f"{values[-1]:.2f}"

    fig, ax = plt.subplots(figsize=(8.8, 3.7), constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    fig.subplots_adjust(left=0.08, right=0.96, top=0.78, bottom=0.12)
    ax.axis("off")
    ax.set_title("Best Observed Parameter Set", loc="left", fontsize=15.5, fontweight="bold", color=NAVY, pad=16)
    ax.text(0.0, 0.93, "Lowest valid BO score after failed and penalty rows are excluded.", transform=ax.transAxes, color=MUTED, fontsize=9.4)

    x0, y0 = 0.0, 0.72
    row_h = 0.105
    for idx, (label, value) in enumerate(zip(labels, display, strict=False)):
        y = y0 - idx * row_h
        bg = "#F3F7FB" if idx % 2 == 0 else "#FFFFFF"
        ax.add_patch(plt.Rectangle((x0, y - 0.045), 0.88, 0.083, transform=ax.transAxes, color=bg, ec="#E1E7EF", lw=0.5))
        ax.text(x0 + 0.025, y, label, transform=ax.transAxes, va="center", ha="left", fontsize=10.2, color=TEXT)
        ax.text(x0 + 0.84, y, value, transform=ax.transAxes, va="center", ha="right", fontsize=10.4, color=NAVY, fontweight="bold")
    pid = str(best.get("parameter_id", ""))
    if pid:
        ax.text(0.0, 0.03, f"Parameter ID: {pid}", transform=ax.transAxes, color=MUTED, fontsize=8.8)
    return save_figure(fig, output_dir, "gp_best_observed_parameter_summary")


def plot_enhanced_partial_dependence(
    observations: pd.DataFrame,
    slices: pd.DataFrame,
    summary: pd.DataFrame,
    best: dict[str, Any],
    output_dir: Path,
) -> list[Path]:
    ymin, ymax = display_limits(observations, slices)
    fig, axes = plt.subplots(2, 3, figsize=(13.4, 7.9), sharey=True, constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    fig.subplots_adjust(left=0.07, right=0.985, bottom=0.10, top=0.80, wspace=0.12, hspace=0.48)
    axes_flat = axes.ravel()

    summary_by_field = summary.set_index("parameter")
    for ax, field in zip(axes_flat[:5], THETA_FIELDS, strict=False):
        prepare_axis(ax, grid_axis="y")
        part = slices[slices["slice_parameter"].eq(field)].sort_values("slice_value")
        x = part["slice_value"].to_numpy(dtype=float)
        mean = part["gp_mean"].to_numpy(dtype=float)
        std = part["gp_std"].to_numpy(dtype=float)
        low = np.clip(mean - std, ymin, ymax)
        high = np.clip(mean + std, ymin, ymax)
        row = summary_by_field.loc[field]

        ax.axvspan(float(row["near_best_low"]), float(row["near_best_high"]), color=GREEN_LIGHT, alpha=0.55, lw=0, zorder=0)
        ax.fill_between(x, low, high, color=BLUE_LIGHT, alpha=0.62, linewidth=0, zorder=1)
        ax.plot(x, np.clip(mean, ymin, ymax), color=BLUE, lw=2.35, zorder=3)
        ax.scatter(
            observations[field],
            observations["score"],
            s=12,
            color="#35475C",
            alpha=0.14,
            linewidth=0,
            zorder=2,
            rasterized=True,
        )
        ax.axvline(float(best[field]), color=ORANGE, lw=1.45, linestyle="--", alpha=0.95, zorder=4)
        ax.scatter([best[field]], [best["score"]], s=62, color=ORANGE, edgecolor=NAVY, linewidth=1.0, zorder=5)
        ax.scatter(
            [row["predicted_best_value"]],
            [row["predicted_min_score"]],
            s=48,
            marker="D",
            color=GREEN,
            edgecolor=NAVY,
            linewidth=0.75,
            zorder=5,
        )

        ax.set_title(f"{LABELS[field]}  |  effect {row['effect_range_score']:.1f}", loc="left", fontsize=11.0, fontweight="bold", pad=7)
        ax.set_xlabel(LABELS[field], fontsize=10.0)
        ax.set_ylabel("Score", fontsize=10.2)
        ax.set_ylim(ymin, ymax)
        ax.xaxis.set_major_locator(MaxNLocator(5))
        ax.tick_params(axis="both", labelsize=9.2)

    panel = axes_flat[-1]
    panel.axis("off")
    panel.set_title("Reading Guide", loc="left", fontsize=11.0, fontweight="bold", color=NAVY, pad=7)
    best_line = f"Best observed score: {float(best['score']):.2f}"
    most = summary.iloc[0]
    least = summary.iloc[-1]
    guide_lines = [
        best_line,
        f"Most sensitive: {most['label']}",
        f"Least sensitive: {least['label']}",
    ]
    for idx, line in enumerate(guide_lines):
        panel.text(0.02, 0.88 - idx * 0.12, line, transform=panel.transAxes, fontsize=9.8, color=TEXT if idx else NAVY, fontweight="bold" if idx == 0 else "normal")

    legend_y = 0.46
    legend_items = [
        ("Near-minimum range", "patch_green"),
        ("Model uncertainty (+/-1 std)", "patch_blue"),
        ("GP mean", "line_blue"),
        ("Observed score", "dot_gray"),
        ("Best observed value", "line_orange"),
        ("Predicted minimum", "diamond_green"),
    ]
    for idx, (label, kind) in enumerate(legend_items):
        y0 = legend_y - idx * 0.075
        if kind == "patch_green":
            panel.add_patch(plt.Rectangle((0.025, y0 - 0.018), 0.055, 0.032, transform=panel.transAxes, facecolor=GREEN_LIGHT, edgecolor="none"))
        elif kind == "patch_blue":
            panel.add_patch(plt.Rectangle((0.025, y0 - 0.018), 0.055, 0.032, transform=panel.transAxes, facecolor=BLUE_LIGHT, edgecolor="none"))
        elif kind == "line_blue":
            panel.plot([0.025, 0.085], [y0, y0], transform=panel.transAxes, color=BLUE, lw=2.4, clip_on=False)
        elif kind == "dot_gray":
            panel.scatter([0.055], [y0], transform=panel.transAxes, s=26, color="#35475C", alpha=0.35, clip_on=False)
        elif kind == "line_orange":
            panel.plot([0.055, 0.055], [y0 - 0.025, y0 + 0.025], transform=panel.transAxes, color=ORANGE, lw=1.6, linestyle="--", clip_on=False)
        elif kind == "diamond_green":
            panel.scatter([0.055], [y0], transform=panel.transAxes, s=36, marker="D", color=GREEN, edgecolor=NAVY, linewidth=0.7, clip_on=False)
        panel.text(0.105, y0, label, transform=panel.transAxes, va="center", ha="left", fontsize=8.9, color=TEXT)

    axes_flat[1].set_ylabel("")
    axes_flat[2].set_ylabel("")
    axes_flat[4].set_ylabel("")

    fig.suptitle("Refit GP Partial Dependence", x=0.035, y=0.965, ha="left", fontsize=16.0, fontweight="bold", color=NAVY)
    fig.text(0.035, 0.918, "GaussianProcessRegressor refit from valid BO observations; lower Score is better.", color=MUTED, fontsize=9.6)
    return save_figure(fig, output_dir, "gp_partial_dependence_enhanced")


def build_pca_projection(observations: pd.DataFrame, output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
    try:
        from sklearn.decomposition import PCA  # type: ignore
        from sklearn.preprocessing import StandardScaler  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("scikit-learn is required for PCA projection") from exc

    x = observations[THETA_FIELDS].astype(float).to_numpy()
    scaled = StandardScaler().fit_transform(x)
    pca = PCA(n_components=2, random_state=0)
    coords = pca.fit_transform(scaled)
    projection = observations.copy()
    projection["PC1"] = coords[:, 0]
    projection["PC2"] = coords[:, 1]
    projection["score_rank"] = projection["score"].rank(method="first", ascending=True).astype(int)
    projection["is_top5"] = projection["score_rank"].le(5)
    projection["is_best"] = projection["score_rank"].eq(1)

    loadings = pd.DataFrame(
        pca.components_.T,
        columns=["PC1_loading", "PC2_loading"],
        index=THETA_FIELDS,
    ).reset_index(names="parameter")
    loadings["label"] = loadings["parameter"].map(LABELS)

    projection.to_csv(output_dir / "pca_bo_projection_source.csv", index=False)
    loadings.to_csv(output_dir / "pca_loadings.csv", index=False)
    return projection, loadings, pca.explained_variance_ratio_


def plot_pca_with_sensitivity(
    observations: pd.DataFrame,
    sensitivity: pd.DataFrame,
    output_dir: Path,
) -> list[Path]:
    projection, loadings, explained = build_pca_projection(observations, output_dir)
    best = projection.loc[projection["score"].idxmin()]
    top5 = projection[projection["is_top5"]].copy()
    ranking = sensitivity.sort_values("effect_range_score", ascending=True)

    fig = plt.figure(figsize=(12.8, 5.8), constrained_layout=False)
    fig.patch.set_facecolor(BACKGROUND)
    gs = fig.add_gridspec(
        nrows=1,
        ncols=2,
        width_ratios=[1.35, 0.92],
        left=0.075,
        right=0.965,
        top=0.78,
        bottom=0.15,
        wspace=0.42,
    )
    ax_map = fig.add_subplot(gs[0, 0])
    ax_rank = fig.add_subplot(gs[0, 1])
    prepare_axis(ax_map, grid_axis="both")
    prepare_axis(ax_rank, grid_axis="x")

    scores = projection["score"].to_numpy(dtype=float)
    vmin = float(np.nanpercentile(scores, 3))
    vmax = float(np.nanpercentile(scores, 97))
    norm = Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = plt.get_cmap("viridis")

    scatter = ax_map.scatter(
        projection["PC1"],
        projection["PC2"],
        c=projection["score"],
        cmap=cmap,
        norm=norm,
        s=34,
        alpha=0.74,
        edgecolor="white",
        linewidth=0.35,
        zorder=2,
    )
    ax_map.scatter(
        top5["PC1"],
        top5["PC2"],
        s=96,
        facecolor="none",
        edgecolor=ORANGE,
        linewidth=1.35,
        zorder=4,
        label="Top 5 observed",
    )
    ax_map.scatter(
        [best["PC1"]],
        [best["PC2"]],
        s=170,
        marker="*",
        color=ORANGE,
        edgecolor=NAVY,
        linewidth=0.95,
        zorder=5,
        label="Best observed",
    )
    ax_map.annotate(
        f"best observed\nScore {float(best['score']):.2f}",
        xy=(best["PC1"], best["PC2"]),
        xytext=(best["PC1"] + 0.55, best["PC2"] + 0.55),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.0),
        fontsize=8.8,
        color=NAVY,
        ha="left",
        va="bottom",
    )

    ax_map.axhline(0, color="#C7D1DC", lw=0.8, zorder=0)
    ax_map.axvline(0, color="#C7D1DC", lw=0.8, zorder=0)
    ax_map.set_title("PCA Projection of BO Search", loc="left", fontsize=14.0, fontweight="bold", pad=14)
    ax_map.set_xlabel(f"PC1 ({explained[0] * 100:.1f}% variance)", fontsize=10.4)
    ax_map.set_ylabel(f"PC2 ({explained[1] * 100:.1f}% variance)", fontsize=10.4)
    ax_map.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)
    cbar = fig.colorbar(scatter, ax=ax_map, fraction=0.038, pad=0.018)
    cbar.ax.set_title("Score\nlower is better", color=TEXT, fontsize=8.5, pad=7)
    cbar.ax.tick_params(labelsize=8.4, colors=MUTED)

    y = np.arange(len(ranking))
    colors = [BLUE if i == len(ranking) - 1 else "#8FB7DF" for i in range(len(ranking))]
    ax_rank.barh(y, ranking["effect_range_score"], height=0.58, color=colors, edgecolor="white", linewidth=0.8)
    for yi, value in zip(y, ranking["effect_range_score"], strict=False):
        ax_rank.text(float(value) + 0.9, yi, f"{value:.1f}", va="center", ha="left", fontsize=8.9, color=TEXT)
    ax_rank.set_yticks(y)
    ax_rank.set_yticklabels(ranking["label"], fontsize=9.4)
    ax_rank.set_xlabel("GP Mean Score Range", fontsize=10.0)
    ax_rank.set_title("Sensitivity Ranking", loc="left", fontsize=14.0, fontweight="bold", pad=14)
    ax_rank.set_xlim(0, max(float(ranking["effect_range_score"].max()) * 1.2, 1.0))
    ax_rank.text(
        0.0,
        -0.22,
        "Ranking keeps original-variable interpretation that PCA alone cannot provide.",
        transform=ax_rank.transAxes,
        fontsize=8.5,
        color=MUTED,
        va="top",
    )

    fig.suptitle("PCA Search Map with Parameter Sensitivity", x=0.075, y=0.95, ha="left", fontsize=16.2, fontweight="bold", color=NAVY)
    fig.text(
        0.075,
        0.89,
        "PCA is a projected view of the five-dimensional BO search history; it is not the full objective surface.",
        color=MUTED,
        fontsize=9.5,
    )
    return save_figure(fig, output_dir, "pca_search_map_with_sensitivity_ranking")


def build(args: argparse.Namespace) -> None:
    configure_style()
    base = load_base_module()
    bo_csv = resolve_path(args.bo_csv)
    snapshot_dir = resolve_path(args.snapshot_dir)
    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(bo_csv)
    observations = base.valid_observations(raw)
    if observations.empty:
        raise ValueError(f"No valid BO observations found: {bo_csv}")
    observations.to_csv(output_dir / "gp_valid_observations.csv", index=False)

    bounds = base.load_bounds(snapshot_dir)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        payload = base.fit_refit_payload(
            observations=observations,
            bounds=bounds,
            output_dir=output_dir,
            seed=args.seed,
            restarts=args.refit_restarts,
        )
    slices, best = base.build_gp_partial_dependence(
        payload=payload,
        observations=observations,
        grid_count=args.grid_count,
        samples=args.partial_dependence_samples,
        seed=args.seed,
    )
    slices.to_csv(output_dir / "gp_partial_dependence_source.csv", index=False)
    summary = build_sensitivity_summary(slices, best)
    summary.to_csv(output_dir / "gp_parameter_sensitivity_summary.csv", index=False)

    generated = []
    generated += plot_pca_with_sensitivity(observations, summary, output_dir)
    generated += plot_sensitivity_ranking(summary, output_dir)
    generated += plot_enhanced_partial_dependence(observations, slices, summary, best, output_dir)
    generated += plot_best_parameter_summary(best, output_dir)

    print(output_dir)
    print("raw_rows", len(raw), "valid_rows", len(observations), "excluded_rows", len(raw) - len(observations))
    print("best_score", f"{float(best['score']):.2f}", "best_parameter_id", best.get("parameter_id", ""))
    print(summary[["label", "effect_range_score", "mean_gp_std", "near_best_low", "near_best_high"]].round(3).to_string(index=False))
    for path in generated:
        print(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build presentation-ready BO refit GP sensitivity figures.")
    parser.add_argument("--bo-csv", default=str(DEFAULT_BO_CSV))
    parser.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--grid-count", type=int, default=160)
    parser.add_argument("--partial-dependence-samples", type=int, default=160)
    parser.add_argument("--seed", type=int, default=20260607)
    parser.add_argument("--refit-restarts", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    build(parse_args())


if __name__ == "__main__":
    main()
