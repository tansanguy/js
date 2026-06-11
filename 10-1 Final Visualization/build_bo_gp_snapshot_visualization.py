#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pickle
import warnings
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BO_CSV = PROJECT_ROOT / "gcpcsv" / "final result" / "reference" / "BO06092213.csv"
DEFAULT_SNAPSHOT_DIR = PROJECT_ROOT / "gcpcsv" / "final result" / "reference"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "figures" / "bo_gp_snapshot_06092213"
DEFAULT_REFIT_OUTPUT_DIR = PROJECT_ROOT / "results" / "figures" / "bo_gp_refit_06092213"

THETA_FIELDS = ["t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau"]
LABELS = {
    "t_lead": "Lead Time (s)",
    "delta_T_thr": "ETA Gate Threshold (s)",
    "G_ext": "Green Extension (s)",
    "Q_ratio": "Queue Ratio",
    "tau": "Spillback Threshold",
}
NAVY = "#0B1F3A"
BLUE = "#2F80C5"
GREEN = "#2A9D78"
ORANGE = "#F2A541"
TEXT = "#25364A"
MUTED = "#6B7788"
GRID = "#D9E2EC"
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
            "legend.fontsize": 9.2,
        }
    )


def resolve_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def safe_float(value: Any, default: float = np.nan) -> float:
    try:
        if value in {"", None}:
            return default
        return float(value)
    except Exception:
        return default


def boolish(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "pass"}


def valid_observations(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in [*THETA_FIELDS, "score", "round", "round_theta_index", "penalty"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    mask = out["score"].notna() & out["score"].lt(10000)
    if "final_status" in out.columns:
        mask &= out["final_status"].fillna("PASS").astype(str).str.upper().eq("PASS")
    if "penalty" in out.columns:
        mask &= out["penalty"].fillna(0).eq(0)
    for field in THETA_FIELDS:
        mask &= out[field].notna()
    return out[mask].sort_values(["round", "round_theta_index"], kind="mergesort").copy()


def load_gp_payload(snapshot_dir: Path, model_path: str | None) -> dict[str, Any]:
    path = resolve_path(model_path) if model_path else snapshot_dir / "final_gp_model.pkl"
    if not path.exists():
        snapshots = sorted((snapshot_dir / "gp_snapshots").glob("round_*_model.pkl"))
        if not snapshots:
            raise FileNotFoundError(f"No GP model pickle found under {snapshot_dir}")
        path = snapshots[-1]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with path.open("rb") as file:
            payload = pickle.load(file)
    if not isinstance(payload, dict) or "model" not in payload:
        raise ValueError(f"Unexpected GP payload format: {path}")
    payload["_model_path"] = path
    return payload


def load_bounds(snapshot_dir: Path) -> dict[str, Any]:
    path = snapshot_dir / "theta_bounds.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    payload = load_gp_payload(snapshot_dir, None)
    return dict(payload["bounds"])


def theta_vector(theta: dict[str, Any], bounds: dict[str, Any]) -> list[float]:
    vector = []
    for field in THETA_FIELDS:
        lower = safe_float(bounds[field]["lower"])
        upper = safe_float(bounds[field]["upper"])
        width = max(upper - lower, 1.0)
        vector.append((safe_float(theta.get(field), lower) - lower) / width)
    return vector


def predict_gp(payload: dict[str, Any], theta_rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    bounds = payload["bounds"]
    x = np.array([theta_vector(row, bounds) for row in theta_rows], dtype=float)
    mean_scaled, std_scaled = payload["model"].predict(x, return_std=True)
    target_mean = float(payload.get("target_mean", 0.0))
    target_std = float(payload.get("target_std", 1.0))
    return mean_scaled.astype(float) * target_std + target_mean, std_scaled.astype(float) * target_std


def fit_refit_payload(
    observations: pd.DataFrame,
    bounds: dict[str, Any],
    output_dir: Path,
    seed: int,
    restarts: int,
) -> dict[str, Any]:
    try:
        from sklearn.gaussian_process import GaussianProcessRegressor  # type: ignore
        from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("scikit-learn is required to refit the GP model") from exc

    theta_rows = observations[THETA_FIELDS].to_dict("records")
    x_train = np.array([theta_vector(row, bounds) for row in theta_rows], dtype=float)
    y_raw = observations["score"].to_numpy(dtype=float)
    y_mean = float(np.mean(y_raw))
    y_std = float(np.std(y_raw))
    if not np.isfinite(y_std) or y_std < 1.0e-9:
        y_std = 1.0
    y_train = (y_raw - y_mean) / y_std
    kernel = ConstantKernel(1.0, constant_value_bounds="fixed") * Matern(
        length_scale=[0.35] * len(THETA_FIELDS),
        length_scale_bounds=(0.03, 2.5),
        nu=2.5,
    ) + WhiteKernel(noise_level=1.0e-4, noise_level_bounds="fixed")
    gp = GaussianProcessRegressor(
        kernel=kernel,
        normalize_y=False,
        random_state=seed,
        alpha=1.0e-4,
        n_restarts_optimizer=restarts,
    )
    gp.fit(x_train, y_train)
    payload = {
        "model": gp,
        "bounds": bounds,
        "theta_fields": THETA_FIELDS,
        "seed": seed,
        "target_mean": y_mean,
        "target_std": y_std,
        "target_was_clipped": False,
        "kernel": str(gp.kernel_),
        "raw_kernel": str(kernel),
        "n_observations": int(len(observations)),
        "n_aggregated_observations": int(len(observations)),
        "training_rows": observations[["parameter_id", *THETA_FIELDS, "score", "round"]].to_dict("records")
        if "parameter_id" in observations.columns
        else observations[[*THETA_FIELDS, "score", "round"]].to_dict("records"),
    }
    path = output_dir / "refit_gp_model.pkl"
    with path.open("wb") as file:
        pickle.dump(payload, file)
    payload["_model_path"] = path
    return payload


def grid_values(bounds: dict[str, Any], field: str, points: int) -> np.ndarray:
    lower = safe_float(bounds[field]["lower"])
    upper = safe_float(bounds[field]["upper"])
    step = bounds[field].get("step")
    if step is not None:
        values = np.arange(lower, upper + safe_float(step) * 0.5, safe_float(step))
    elif "quantized_1s" in str(bounds[field].get("type", "")):
        values = np.arange(lower, upper + 0.5, 1.0)
    else:
        values = np.linspace(lower, upper, points)
    if len(values) > points:
        indices = np.linspace(0, len(values) - 1, points).round().astype(int)
        values = values[indices]
    if field in {"t_lead", "delta_T_thr", "G_ext"}:
        values = np.round(values)
    return np.unique(np.clip(values, lower, upper))


def build_gp_slices(payload: dict[str, Any], observations: pd.DataFrame, grid_count: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    bounds = payload["bounds"]
    best = observations.loc[observations["score"].idxmin()]
    anchor = {field: safe_float(best[field]) for field in THETA_FIELDS}

    rows: list[dict[str, Any]] = []
    for field in THETA_FIELDS:
        theta_rows = []
        for value in grid_values(bounds, field, grid_count):
            theta = dict(anchor)
            theta[field] = float(value)
            theta_rows.append(theta)
        mean, std = predict_gp(payload, theta_rows)
        for theta, gp_mean, gp_std in zip(theta_rows, mean, std):
            rows.append(
                {
                    "slice_parameter": field,
                    "slice_value": theta[field],
                    **theta,
                    "gp_mean": float(gp_mean),
                    "gp_std": float(gp_std),
                    "gp_ci_low": float(gp_mean - 1.96 * gp_std),
                    "gp_ci_high": float(gp_mean + 1.96 * gp_std),
                    "anchor_parameter_id": str(best.get("parameter_id", "")),
                    "anchor_score": float(best["score"]),
                }
            )
    return pd.DataFrame(rows), {
        "parameter_id": str(best.get("parameter_id", "")),
        "score": float(best["score"]),
        **anchor,
    }


def build_gp_partial_dependence(
    payload: dict[str, Any],
    observations: pd.DataFrame,
    grid_count: int,
    samples: int,
    seed: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    bounds = payload["bounds"]
    best = observations.loc[observations["score"].idxmin()]
    rng = np.random.default_rng(seed)
    sample_count = min(max(1, samples), len(observations))
    sampled_indices = rng.choice(observations.index.to_numpy(), size=sample_count, replace=len(observations) < sample_count)
    anchors = observations.loc[sampled_indices, THETA_FIELDS].to_dict("records")

    rows: list[dict[str, Any]] = []
    for field in THETA_FIELDS:
        for value in grid_values(bounds, field, grid_count):
            theta_rows = []
            for anchor in anchors:
                theta = {name: safe_float(anchor[name]) for name in THETA_FIELDS}
                theta[field] = float(value)
                theta_rows.append(theta)
            mean, std = predict_gp(payload, theta_rows)
            finite_mean = mean[np.isfinite(mean)]
            finite_std = std[np.isfinite(std)]
            if len(finite_mean) == 0 or len(finite_std) == 0:
                continue
            pd_mean = float(np.mean(finite_mean))
            pd_var = float(np.mean(np.square(finite_std)))
            pd_std = float(np.sqrt(max(pd_var, 0.0)))
            rows.append(
                {
                    "slice_parameter": field,
                    "slice_value": float(value),
                    "gp_mean": pd_mean,
                    "gp_std": pd_std,
                    "gp_ci_low": pd_mean - 1.96 * pd_std,
                    "gp_ci_high": pd_mean + 1.96 * pd_std,
                    "partial_dependence_samples": sample_count,
                    "anchor_parameter_id": str(best.get("parameter_id", "")),
                    "anchor_score": float(best["score"]),
                }
            )
    return pd.DataFrame(rows), {
        "parameter_id": str(best.get("parameter_id", "")),
        "score": float(best["score"]),
        **{field: safe_float(best[field]) for field in THETA_FIELDS},
    }


def display_limits(observations: pd.DataFrame, slices: pd.DataFrame | None = None) -> tuple[float, float]:
    observed = observations["score"].to_numpy(dtype=float)
    values = observed[np.isfinite(observed)]
    low = float(np.nanpercentile(values, 1))
    high = float(np.nanpercentile(values, 98))
    if high <= low:
        high = low + max(abs(low) * 0.1, 1.0)
    pad = max((high - low) * 0.18, 6.0)
    return max(0.0, low - pad), high + pad


def prepare_axes(ax: plt.Axes) -> None:
    ax.set_facecolor(BACKGROUND)
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    ax.yaxis.set_major_locator(MaxNLocator(5))


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> list[str]:
    paths = []
    for ext in ["png", "svg"]:
        path = output_dir / f"{stem}.{ext}"
        fig.savefig(path, bbox_inches="tight")
        paths.append(str(path.relative_to(PROJECT_ROOT)))
    plt.close(fig)
    return paths


def plot_convergence(observations: pd.DataFrame, output_dir: Path) -> list[str]:
    round_summary = (
        observations.groupby("round", sort=True)
        .agg(round_best_score=("score", "min"), valid_theta_count=("score", "size"))
        .reset_index()
    )
    round_summary["best_so_far_score"] = round_summary["round_best_score"].cummin()
    round_summary.to_csv(output_dir / "bo_convergence_round_summary.csv", index=False)

    fig, ax = plt.subplots(figsize=(10.8, 5.8), constrained_layout=True)
    fig.patch.set_facecolor(BACKGROUND)
    prepare_axes(ax)
    ax.scatter(observations["round"], observations["score"], s=20, color=NAVY, alpha=0.35, label="Observed score")
    ax.step(
        round_summary["round"],
        round_summary["best_so_far_score"],
        where="post",
        color=BLUE,
        lw=2.8,
        label="Best-so-far score",
    )
    best = observations.loc[observations["score"].idxmin()]
    ax.scatter([best["round"]], [best["score"]], s=88, color=ORANGE, edgecolor=NAVY, linewidth=1.1, zorder=5, label="Best observed")
    ax.text(float(best["round"]) + 0.7, float(best["score"]), f"{best['score']:.2f}", color=BLUE, fontsize=9.5, fontweight="bold", va="center")
    ymin, ymax = display_limits(observations, pd.DataFrame({"gp_mean": []}))
    ax.set_ylim(ymin, ymax)
    ax.set_xlim(1, float(round_summary["round"].max()) + 1.5)
    ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
    ax.set_title("BO Convergence", loc="left", fontsize=17, fontweight="bold", pad=14)
    ax.set_xlabel("Round", fontsize=11.5)
    ax.set_ylabel("Score", fontsize=11.5)
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)
    return save_figure(fig, output_dir, "bo_convergence")


def plot_gp_slices(
    observations: pd.DataFrame,
    slices: pd.DataFrame,
    best: dict[str, Any],
    output_dir: Path,
    title: str,
    stem: str,
) -> list[str]:
    ymin, ymax = display_limits(observations, slices)
    fig, axes = plt.subplots(2, 3, figsize=(13.2, 7.8), sharey=True, constrained_layout=False)
    fig.subplots_adjust(left=0.07, right=0.985, bottom=0.10, top=0.80, wspace=0.10, hspace=0.48)
    fig.patch.set_facecolor(BACKGROUND)
    axes_flat = axes.ravel()

    for axis, field in zip(axes_flat, THETA_FIELDS):
        prepare_axes(axis)
        rows = slices[slices["slice_parameter"] == field].sort_values("slice_value")
        x = rows["slice_value"].to_numpy(dtype=float)
        mean = rows["gp_mean"].to_numpy(dtype=float)
        low = np.clip(rows["gp_ci_low"].to_numpy(dtype=float), ymin, ymax)
        high = np.clip(rows["gp_ci_high"].to_numpy(dtype=float), ymin, ymax)
        mean_clipped = np.clip(mean, ymin, ymax)
        axis.fill_between(x, low, high, color=BLUE, alpha=0.14, linewidth=0, label="95% GP confidence interval")
        axis.plot(x, mean_clipped, color=BLUE, lw=2.4, label="GP mean")
        axis.scatter(
            observations[field],
            observations["score"],
            s=14,
            color="#53657A",
            alpha=0.16,
            linewidth=0,
            label="Observed PASS score",
        )
        axis.axvline(best[field], color=ORANGE, lw=1.4, linestyle="--", alpha=0.95)
        axis.scatter(
            [best[field]],
            [best["score"]],
            s=64,
            color=ORANGE,
            edgecolor=NAVY,
            linewidth=1.0,
            zorder=5,
            label="Best observed",
        )
        axis.set_ylim(ymin, ymax)
        axis.set_title(LABELS[field], loc="left", fontsize=11.8, fontweight="bold", pad=7)
        axis.set_xlabel(LABELS[field], fontsize=10.2)
        axis.set_ylabel("Score", fontsize=10.2)
        axis.xaxis.set_major_locator(MaxNLocator(5))
        axis.tick_params(axis="both", labelsize=9.4)

    axes_flat[-1].axis("off")
    for axis in axes_flat[1:3]:
        axis.set_ylabel("")
    for axis in axes_flat[4:]:
        axis.set_ylabel("")
    handles, labels = axes_flat[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="lower right",
        bbox_to_anchor=(0.982, 0.09),
        frameon=True,
        facecolor="white",
        edgecolor="#E1E7EF",
        framealpha=0.96,
    )
    fig.suptitle(title, x=0.035, y=0.965, ha="left", fontsize=15.2, fontweight="bold", color=NAVY)
    fig.text(0.035, 0.918, "GaussianProcessRegressor refit from valid BO observations; lower Score is better.", color=MUTED, fontsize=9.6)
    return save_figure(fig, output_dir, stem)


def write_html(output_dir: Path, generated: dict[str, list[str]], manifest_name: str) -> Path:
    rel_images = {
        "convergence": "bo_convergence.png",
        "gp_slices": "final_gp_parameter_slices.png",
    }
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>BO GP Visualization</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #0B1F3A; background: #FBFCFD; }}
    main {{ max-width: 1180px; margin: 32px auto 56px; padding: 0 28px; }}
    h1 {{ font-size: 34px; margin: 0 0 22px; }}
    h2 {{ font-size: 22px; margin: 34px 0 12px; }}
    img {{ width: 100%; height: auto; border: 1px solid #E1E7EF; background: white; }}
    a {{ color: #2F80C5; }}
  </style>
</head>
<body>
  <main>
    <h1>BO GP Visualization</h1>
    <h2>BO Convergence</h2>
    <img src="{rel_images['convergence']}" alt="BO Convergence">
    <h2>Final GP Parameter Slices</h2>
    <img src="{rel_images['gp_slices']}" alt="Final GP Parameter Slices">
    <p><a href="{manifest_name}">Manifest</a></p>
  </main>
</body>
</html>
"""
    path = output_dir / "bo_visualization_two_types.html"
    path.write_text(html, encoding="utf-8")
    return path


def build(args: argparse.Namespace) -> dict[str, Any]:
    configure_style()
    bo_csv = resolve_path(args.bo_csv)
    snapshot_dir = resolve_path(args.snapshot_dir)
    if args.output_dir:
        output_dir = resolve_path(args.output_dir)
    else:
        output_dir = DEFAULT_REFIT_OUTPUT_DIR if args.gp_source == "refit" else DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(bo_csv)
    observations = valid_observations(raw)
    if observations.empty:
        raise ValueError(f"No valid PASS observations in {bo_csv}")
    observations.to_csv(output_dir / "bo_valid_observations.csv", index=False)

    if args.gp_source == "refit":
        payload = fit_refit_payload(
            observations=observations,
            bounds=load_bounds(snapshot_dir),
            output_dir=output_dir,
            seed=args.seed,
            restarts=args.refit_restarts,
        )
        slices, best = build_gp_partial_dependence(
            payload=payload,
            observations=observations,
            grid_count=args.grid_count,
            samples=args.partial_dependence_samples,
            seed=args.seed,
        )
        slice_title = "Refit GP Partial Dependence"
    else:
        payload = load_gp_payload(snapshot_dir, args.model_path)
        slices, best = build_gp_slices(payload, observations, args.grid_count)
        slice_title = "Final GP Parameter Slices"
    slices.to_csv(output_dir / "final_gp_parameter_slices.csv", index=False)

    generated = {
        "bo_convergence": plot_convergence(observations, output_dir),
        "final_gp_parameter_slices": plot_gp_slices(
            observations,
            slices,
            best,
            output_dir,
            title=slice_title,
            stem="final_gp_parameter_slices",
        ),
    }

    manifest = {
        "bo_csv": str(bo_csv.relative_to(PROJECT_ROOT) if bo_csv.is_relative_to(PROJECT_ROOT) else bo_csv),
        "snapshot_dir": str(snapshot_dir.relative_to(PROJECT_ROOT) if snapshot_dir.is_relative_to(PROJECT_ROOT) else snapshot_dir),
        "gp_source": args.gp_source,
        "model_path": str(payload["_model_path"].relative_to(PROJECT_ROOT) if payload["_model_path"].is_relative_to(PROJECT_ROOT) else payload["_model_path"]),
        "model_kernel": str(payload.get("kernel", "")),
        "raw_kernel": str(payload.get("raw_kernel", "")),
        "target_mean": float(payload.get("target_mean", np.nan)),
        "target_std": float(payload.get("target_std", np.nan)),
        "target_was_clipped": bool(payload.get("target_was_clipped", False)),
        "n_observations_in_csv": int(len(raw)),
        "n_valid_observations_plotted": int(len(observations)),
        "n_model_observations": int(payload.get("n_observations", -1)),
        "n_aggregated_model_observations": int(payload.get("n_aggregated_observations", -1)),
        "partial_dependence_samples": int(args.partial_dependence_samples) if args.gp_source == "refit" else None,
        "confidence_interval_definition": "refit mode: mean over sampled non-target dimensions +/- 1.96 * sqrt(mean(GP predictive variance)); snapshot mode: fixed-best 1D conditional slice +/- 1.96 * GP std",
        "anchor_best_observed": best,
        "generated": generated,
    }
    manifest_path = output_dir / "bo_gp_visualization_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    html_path = write_html(output_dir, generated, manifest_path.name)
    manifest["generated"]["html"] = [str(html_path.relative_to(PROJECT_ROOT))]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("Generated:")
    for name, paths in manifest["generated"].items():
        print(f"- {name}: {', '.join(paths)}")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build BO convergence and final GP parameter-slice visualizations.")
    parser.add_argument("--bo-csv", default=str(DEFAULT_BO_CSV), help="BO evaluation CSV with theta and score columns.")
    parser.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR), help="Directory containing final_gp_model.pkl and theta bounds.")
    parser.add_argument("--model-path", default=None, help="Optional explicit GP model pickle path.")
    parser.add_argument("--gp-source", choices=["snapshot", "refit"], default="snapshot", help="Use saved GP payload or refit a deterministic GP from the BO CSV.")
    parser.add_argument("--output-dir", default=None, help="Output directory for figures and clean tables.")
    parser.add_argument("--grid-count", type=int, default=160, help="Maximum grid points per theta variable.")
    parser.add_argument("--partial-dependence-samples", type=int, default=160, help="Observed theta samples used to marginalize other variables in refit mode.")
    parser.add_argument("--seed", type=int, default=20260607, help="Random seed for GP refit and partial-dependence sampling.")
    parser.add_argument("--refit-restarts", type=int, default=5, help="Optimizer restarts for the refit GaussianProcessRegressor.")
    return parser.parse_args()


def main() -> None:
    build(parse_args())


if __name__ == "__main__":
    main()
