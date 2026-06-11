#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPT_OUTPUT_ROOT = PROJECT_ROOT / "09-1 B4 Optimization S1forced" / "outputs"
LATEST_JSON = OPT_OUTPUT_ROOT / "latest.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "results" / "figures" / "final_optimization_results"

METHOD_ORDER = ["BO", "CMA-ES", "Random Search"]
COLORS = {
    "BO": "#2F80C5",
    "CMA-ES": "#2A9D78",
    "Random Search": "#C96B42",
}
NAVY = "#0B1F3A"
TEXT = "#25364A"
MUTED = "#6B7788"
GRID = "#D9E2EC"
ORANGE = "#F2A541"
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
            "legend.fontsize": 9.5,
        }
    )


def resolve_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_latest() -> dict[str, Any]:
    if not LATEST_JSON.exists():
        return {}
    return json.loads(LATEST_JSON.read_text(encoding="utf-8"))


def resolve_run_dir(arg_run_dir: str | None, latest: dict[str, Any]) -> Path:
    if arg_run_dir:
        path = resolve_path(arg_run_dir)
        if path.is_file():
            return path.parent
        return path
    output_dir = latest.get("output_dir")
    if output_dir:
        return resolve_path(str(output_dir))
    raise FileNotFoundError(f"Cannot resolve run directory because {LATEST_JSON} has no output_dir")


def resolve_input(run_dir: Path, latest: dict[str, Any], key: str, filename: str) -> Path | None:
    candidates = [run_dir / filename]
    latest_value = latest.get(key)
    if latest_value:
        candidates.append(resolve_path(str(latest_value)))
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None


def read_csv(path: Path | None) -> pd.DataFrame | None:
    if path is None:
        return None
    return pd.read_csv(path)


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def first_existing(df: pd.DataFrame, names: list[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def normalize_method_name(value: Any) -> str:
    text = str(value).strip()
    lowered = text.lower().replace("_", " ").replace("-", " ")
    if lowered in {"bo", "bayesian optimization", "bayesian"}:
        return "BO"
    if lowered in {"cma es", "cma", "cms"}:
        return "CMA-ES"
    if lowered in {"random search", "random", "rs"}:
        return "Random Search"
    return text


def valid_score_mask(df: pd.DataFrame) -> pd.Series:
    mask = df["score"].notna() & np.isfinite(df["score"]) & df["score"].lt(10000)
    if "final_status" in df.columns:
        mask &= df["final_status"].fillna("PASS").astype(str).str.upper().eq("PASS")
    if "penalty" in df.columns:
        mask &= numeric(df["penalty"]).fillna(0).eq(0)
    return mask


def normalize_evaluations(final_method: pd.DataFrame | None, all_evaluations: pd.DataFrame | None) -> pd.DataFrame:
    source = final_method if final_method is not None and not final_method.empty else all_evaluations
    if source is None or source.empty:
        return pd.DataFrame()

    df = source.copy()
    mapping = {
        "method": first_existing(df, ["input_method", "method", "optimizer_method"]),
        "seed": first_existing(df, ["input_seed", "seed"]),
        "round": first_existing(df, ["input_round", "round", "presentation_round"]),
        "parameter_id": first_existing(df, ["input_parameter_id", "parameter_id"]),
        "score": first_existing(df, ["score", "observed_score"]),
        "D_E_sec": first_existing(
            df,
            ["output_D_E_sec", "output_delay_A_sec", "D_E_sec", "measured_D_E_sec", "measured_d_EMV_sec", "delay_A"],
        ),
        "D_G_sec": first_existing(
            df,
            ["output_D_G_sec", "output_delay_N_sec", "D_G_sec", "measured_D_G_sec", "measured_d_veh_sec", "delay_N"],
        ),
        "t_lead": first_existing(df, ["input_t_lead", "t_lead"]),
        "delta_T_thr": first_existing(df, ["input_delta_T_thr", "delta_T_thr"]),
        "G_ext": first_existing(df, ["input_G_ext", "G_ext"]),
        "Q_ratio": first_existing(df, ["input_Q_ratio", "Q_ratio"]),
        "tau": first_existing(df, ["input_tau", "tau"]),
        "final_status": first_existing(df, ["final_status"]),
        "penalty": first_existing(df, ["penalty"]),
    }
    required = ["method", "round", "score"]
    missing = [name for name in required if mapping[name] is None]
    if missing:
        raise ValueError(f"Evaluation table is missing required columns: {', '.join(missing)}")

    out = pd.DataFrame()
    out["method"] = df[mapping["method"]].map(normalize_method_name)
    out["seed"] = df[mapping["seed"]] if mapping["seed"] else ""
    out["round"] = numeric(df[mapping["round"]])
    out["parameter_id"] = df[mapping["parameter_id"]].astype(str) if mapping["parameter_id"] else ""
    out["score"] = numeric(df[mapping["score"]])
    for name in ["D_E_sec", "D_G_sec", "t_lead", "delta_T_thr", "G_ext", "Q_ratio", "tau", "penalty"]:
        out[name] = numeric(df[mapping[name]]) if mapping[name] else np.nan
    out["final_status"] = df[mapping["final_status"]].astype(str) if mapping["final_status"] else "PASS"
    out = out[out["round"].notna()].copy()
    out["round"] = out["round"].astype(int)
    out["is_valid"] = valid_score_mask(out)
    out["source_row"] = np.arange(1, len(out) + 1)
    return out.sort_values(["method", "seed", "round", "source_row"], kind="mergesort")


def build_round_summary(evals: pd.DataFrame) -> pd.DataFrame:
    valid = evals[evals["is_valid"]].copy()
    if valid.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for method, group in valid.groupby("method", sort=False):
        for round_id, round_group in group.groupby("round", sort=True):
            best_idx = round_group["score"].idxmin()
            best = round_group.loc[best_idx]
            rows.append(
                {
                    "method": method,
                    "round": int(round_id),
                    "round_best_score": float(best["score"]),
                    "round_best_parameter_id": best["parameter_id"],
                    "valid_candidate_count": int(len(round_group)),
                }
            )
    summary = pd.DataFrame(rows).sort_values(["method", "round"], kind="mergesort")
    summary["best_so_far_score"] = (
        summary.groupby("method", sort=False)["round_best_score"].cummin()
    )
    return summary


def clean_bo_surrogate(table: pd.DataFrame | None) -> pd.DataFrame:
    if table is None or table.empty:
        return pd.DataFrame()

    df = table.copy()
    mapping = {
        "round": first_existing(df, ["round", "input_round"]),
        "round_theta_index": first_existing(df, ["round_theta_index", "theta_index_in_round"]),
        "parameter_id": first_existing(df, ["parameter_id", "input_parameter_id"]),
        "observed_score": first_existing(df, ["observed_score", "score"]),
        "best_so_far": first_existing(df, ["best_so_far", "best_so_far_score"]),
        "surrogate_mean": first_existing(df, ["surrogate_mean"]),
        "surrogate_ci_low": first_existing(df, ["surrogate_ci_low"]),
        "surrogate_ci_high": first_existing(df, ["surrogate_ci_high"]),
    }
    if mapping["observed_score"] is None:
        return pd.DataFrame()

    out = pd.DataFrame()
    for name, source_col in mapping.items():
        if source_col is None:
            out[name] = np.nan if name != "parameter_id" else ""
        elif name == "parameter_id":
            out[name] = df[source_col].astype(str)
        else:
            out[name] = numeric(df[source_col])
    out = out[out["observed_score"].notna() & out["observed_score"].lt(10000)].copy()
    out = out.sort_values(["round", "round_theta_index"], kind="mergesort")
    out["bo_evaluation"] = np.arange(1, len(out) + 1)
    return out


def clean_pareto(table: pd.DataFrame | None) -> pd.DataFrame:
    if table is None or table.empty:
        return pd.DataFrame()

    df = table.copy()
    mapping = {
        "weight_ratio": first_existing(df, ["weight_ratio"]),
        "parameter_id": first_existing(df, ["parameter_id", "input_parameter_id"]),
        "D_E_sec": first_existing(df, ["D_E_sec", "output_D_E_sec", "output_delay_A_sec", "delay_A", "measured_D_E_sec", "measured_d_EMV_sec"]),
        "D_G_sec": first_existing(df, ["D_G_sec", "output_D_G_sec", "output_delay_N_sec", "delay_N", "measured_D_G_sec", "measured_d_veh_sec"]),
        "score": first_existing(df, ["score"]),
        "is_knee": first_existing(df, ["is_knee"]),
    }
    if mapping["D_E_sec"] is None or mapping["D_G_sec"] is None:
        return pd.DataFrame()

    out = pd.DataFrame()
    out["weight_ratio"] = df[mapping["weight_ratio"]].astype(str) if mapping["weight_ratio"] else ""
    out["parameter_id"] = df[mapping["parameter_id"]].astype(str) if mapping["parameter_id"] else ""
    out["D_E_sec"] = numeric(df[mapping["D_E_sec"]])
    out["D_G_sec"] = numeric(df[mapping["D_G_sec"]])
    out["score"] = numeric(df[mapping["score"]]) if mapping["score"] else np.nan
    if mapping["is_knee"]:
        raw_knee = df[mapping["is_knee"]]
        out["is_knee"] = raw_knee.astype(str).str.lower().isin(["true", "1", "yes"])
    else:
        out["is_knee"] = False
    out = out[out["D_E_sec"].notna() & out["D_G_sec"].notna()].copy()
    return out


def robust_limits(values: pd.Series | np.ndarray, pad_ratio: float = 0.12) -> tuple[float, float]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return 0.0, 1.0
    low = float(np.nanmin(arr))
    high = float(np.nanmax(arr))
    if np.isclose(low, high):
        pad = max(abs(low) * 0.08, 1.0)
        return max(0.0, low - pad), high + pad
    spread = high - low
    return max(0.0, low - spread * pad_ratio), high + spread * pad_ratio


def trimmed_upper_limits(values: pd.Series | np.ndarray) -> tuple[float, float]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return 0.0, 1.0
    low = float(np.nanmin(arr))
    upper = float(np.nanpercentile(arr, 95))
    high = min(float(np.nanmax(arr)), upper + max((upper - low) * 0.22, 8.0))
    if high <= low:
        high = low + max(abs(low) * 0.1, 1.0)
    return max(0.0, low - (high - low) * 0.12), high + (high - low) * 0.08


def ordered_methods(df: pd.DataFrame) -> list[str]:
    present = set(df["method"].dropna().astype(str))
    ordered = [method for method in METHOD_ORDER if method in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def prepare_axes(ax: plt.Axes) -> None:
    ax.set_facecolor(BACKGROUND)
    ax.grid(True, axis="y", color=GRID, linewidth=0.9)
    ax.grid(False, axis="x")
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#D3DAE2")
    ax.xaxis.set_major_locator(MaxNLocator(8, integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(7))


def save_figure(fig: plt.Figure, output_dir: Path, stem: str) -> list[str]:
    paths = []
    for ext in ["png", "svg"]:
        path = output_dir / f"{stem}.{ext}"
        fig.savefig(path, bbox_inches="tight")
        paths.append(str(path.relative_to(PROJECT_ROOT)))
    plt.close(fig)
    return paths


def plot_convergence(rounds: pd.DataFrame, output_dir: Path) -> list[str]:
    fig, ax = plt.subplots(figsize=(11.4, 6.2), constrained_layout=True)
    fig.patch.set_facecolor(BACKGROUND)
    prepare_axes(ax)

    for method in ordered_methods(rounds):
        curve = rounds[rounds["method"] == method].sort_values("round")
        x = curve["round"].to_numpy(dtype=float)
        y = curve["best_so_far_score"].to_numpy(dtype=float)
        color = COLORS.get(method, NAVY)
        ax.step(x, y, where="post", lw=2.8, color=color, label=method)
        last = curve[curve["best_so_far_score"].notna()].iloc[-1]
        ax.scatter([last["round"]], [last["best_so_far_score"]], s=48, color=color, edgecolor="white", linewidth=1.2, zorder=5)
        ax.text(
            float(last["round"]) + 0.6,
            float(last["best_so_far_score"]),
            f"{last['best_so_far_score']:.2f}",
            color=color,
            fontsize=9.5,
            fontweight="bold",
            va="center",
        )

    ymin, ymax = robust_limits(rounds["best_so_far_score"])
    ax.set_ylim(ymin, ymax)
    ax.set_xlim(1, float(rounds["round"].max()) + 3)
    ax.set_title("Optimization Convergence", loc="left", fontsize=17, fontweight="bold", pad=14)
    ax.set_xlabel("Round", fontsize=11.5)
    ax.set_ylabel("Best-so-far Score", fontsize=11.5)
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)
    return save_figure(fig, output_dir, "optimization_convergence")


def plot_round_best(rounds: pd.DataFrame, output_dir: Path) -> list[str]:
    fig, ax = plt.subplots(figsize=(11.4, 6.2), constrained_layout=True)
    fig.patch.set_facecolor(BACKGROUND)
    prepare_axes(ax)

    for method in ordered_methods(rounds):
        curve = rounds[rounds["method"] == method].sort_values("round")
        ax.plot(
            curve["round"],
            curve["round_best_score"],
            color=COLORS.get(method, NAVY),
            lw=2.25,
            marker="o",
            markersize=4.7,
            label=method,
        )

    ymin, ymax = trimmed_upper_limits(rounds["round_best_score"])
    ax.set_ylim(ymin, ymax)
    ax.set_xlim(1, float(rounds["round"].max()) + 1.5)
    ax.set_title("Round-best Score by Method", loc="left", fontsize=17, fontweight="bold", pad=14)
    ax.set_xlabel("Round", fontsize=11.5)
    ax.set_ylabel("Round-best Score", fontsize=11.5)
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)
    return save_figure(fig, output_dir, "round_best_score_by_method")


def plot_bo_surrogate(bo: pd.DataFrame, output_dir: Path) -> list[str]:
    fig, ax = plt.subplots(figsize=(11.4, 6.2), constrained_layout=True)
    fig.patch.set_facecolor(BACKGROUND)
    prepare_axes(ax)

    ymin, ymax = trimmed_upper_limits(bo["observed_score"])
    surrogate = bo.dropna(subset=["surrogate_mean", "surrogate_ci_low", "surrogate_ci_high"]).copy()
    if not surrogate.empty:
        x = surrogate["bo_evaluation"].to_numpy(dtype=float)
        mean = surrogate["surrogate_mean"].to_numpy(dtype=float)
        low = surrogate["surrogate_ci_low"].to_numpy(dtype=float)
        high = surrogate["surrogate_ci_high"].to_numpy(dtype=float)
        valid_ci = np.isfinite(x) & np.isfinite(mean) & np.isfinite(low) & np.isfinite(high)
        x, mean, low, high = x[valid_ci], mean[valid_ci], low[valid_ci], high[valid_ci]
        if len(x):
            clipped_low = np.clip(low, ymin, ymax)
            clipped_high = np.clip(high, ymin, ymax)
            clipped_mean = np.clip(mean, ymin, ymax)
            ax.fill_between(x, clipped_low, clipped_high, color=COLORS["BO"], alpha=0.14, linewidth=0, label="Surrogate CI")
            ax.plot(x, clipped_mean, color=COLORS["BO"], lw=2.3, label="Surrogate mean")

    ax.scatter(bo["bo_evaluation"], bo["observed_score"], s=20, color=NAVY, alpha=0.72, label="Observed score", zorder=4)
    best = bo.loc[bo["observed_score"].idxmin()]
    ax.scatter([best["bo_evaluation"]], [best["observed_score"]], s=88, color=ORANGE, edgecolor=NAVY, linewidth=1.2, zorder=6, label="Best observed")
    text_x = min(float(best["bo_evaluation"]) + max(float(bo["bo_evaluation"].max()) * 0.05, 4), float(bo["bo_evaluation"].max()))
    annotation_y = min(
        float(best["observed_score"]) + max((ymax - ymin) * 0.14, 8.0),
        ymax - max((ymax - ymin) * 0.08, 4.0),
    )
    ax.annotate(
        f"Best observed: {best['observed_score']:.2f}",
        xy=(best["bo_evaluation"], best["observed_score"]),
        xytext=(text_x, annotation_y),
        arrowprops=dict(arrowstyle="-|>", color=NAVY, lw=1.1),
        fontsize=9.5,
        color=NAVY,
        ha="left",
        va="center",
    )

    ax.set_ylim(ymin, ymax)
    ax.set_xlim(1, float(bo["bo_evaluation"].max()) + 4)
    ax.set_title("Bayesian Optimization Surrogate Trace", loc="left", fontsize=17, fontweight="bold", pad=14)
    ax.set_xlabel("BO Evaluation", fontsize=11.5)
    ax.set_ylabel("Score", fontsize=11.5)
    ax.legend(loc="upper right", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)
    return save_figure(fig, output_dir, "bo_surrogate_trace")


def plot_delay_tradeoff(pareto: pd.DataFrame, output_dir: Path) -> list[str]:
    fig, ax = plt.subplots(figsize=(8.8, 6.8), constrained_layout=True)
    fig.patch.set_facecolor(BACKGROUND)
    prepare_axes(ax)
    ax.xaxis.set_major_locator(MaxNLocator(7))

    plot_points = (
        pareto.assign(x_round=pareto["D_E_sec"].round(3), y_round=pareto["D_G_sec"].round(3))
        .groupby(["x_round", "y_round"], as_index=False)
        .agg(
            D_E_sec=("D_E_sec", "mean"),
            D_G_sec=("D_G_sec", "mean"),
            weight_ratio=("weight_ratio", lambda values: ", ".join([str(v) for v in values if str(v).strip()])),
            is_knee=("is_knee", "max"),
        )
    )
    ax.scatter(
        plot_points["D_E_sec"],
        plot_points["D_G_sec"],
        s=76,
        color=COLORS["BO"],
        edgecolor="white",
        linewidth=1.1,
        zorder=4,
        label="Pareto candidate",
    )
    for _, row in plot_points.iterrows():
        label = str(row["weight_ratio"]).strip()
        if label:
            ax.text(float(row["D_E_sec"]) + 1.2, float(row["D_G_sec"]) + 1.2, label, fontsize=8.8, color=TEXT)

    knee = plot_points[plot_points["is_knee"]]
    if not knee.empty:
        ax.scatter(
            knee["D_E_sec"],
            knee["D_G_sec"],
            s=150,
            marker="*",
            color=ORANGE,
            edgecolor=NAVY,
            linewidth=0.9,
            zorder=6,
            label="Knee point",
        )

    xmin, xmax = robust_limits(pareto["D_E_sec"], pad_ratio=0.16)
    ymin, ymax = robust_limits(pareto["D_G_sec"], pad_ratio=0.16)
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_title("Emergency-General Delay Trade-off", loc="left", fontsize=17, fontweight="bold", pad=14)
    ax.set_xlabel("Emergency Delay (s)", fontsize=11.5)
    ax.set_ylabel("General Traffic Delay (s)", fontsize=11.5)
    ax.legend(loc="best", frameon=True, facecolor="white", edgecolor="#E1E7EF", framealpha=0.96)
    return save_figure(fig, output_dir, "emergency_general_delay_tradeoff")


def best_parameter_row(evals: pd.DataFrame, pareto: pd.DataFrame) -> pd.Series | None:
    valid = evals[evals["is_valid"]].copy() if not evals.empty else pd.DataFrame()
    if not valid.empty:
        return valid.loc[valid["score"].idxmin()]
    if not pareto.empty and pareto["score"].notna().any():
        return pareto.loc[pareto["score"].idxmin()]
    return None


def plot_best_parameter_summary(best: pd.Series, output_dir: Path) -> list[str]:
    fields = [
        ("t_lead", "t_lead"),
        ("delta_T_thr", "delta_T_thr"),
        ("G_ext", "G_ext"),
        ("Q_ratio", "Q_ratio"),
        ("tau", "tau"),
        ("D_E", "D_E_sec"),
        ("D_G", "D_G_sec"),
        ("Score", "score"),
    ]
    labels = [label for label, _ in fields]
    values = [float(best.get(col, np.nan)) for _, col in fields]
    display = ["-" if not np.isfinite(value) else f"{value:.2f}" for value in values]
    scaled = np.array([0.0 if not np.isfinite(value) else value for value in values], dtype=float)
    max_value = float(np.nanmax(scaled)) if len(scaled) else 1.0
    plot_values = scaled / max(max_value, 1.0)

    fig, ax = plt.subplots(figsize=(9.8, 5.8), constrained_layout=True)
    fig.patch.set_facecolor(BACKGROUND)
    ax.set_facecolor(BACKGROUND)
    y = np.arange(len(labels))
    ax.barh(y, plot_values, color="#7AA6C8", edgecolor="white", height=0.58)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.18)
    ax.set_xticks([])
    ax.spines[["top", "right", "bottom"]].set_visible(False)
    ax.spines["left"].set_color("#D3DAE2")
    ax.set_title("Best Parameter Set", loc="left", fontsize=17, fontweight="bold", pad=14)

    method = str(best.get("method", "")).strip()
    parameter_id = str(best.get("parameter_id", "")).strip()
    subtitle_parts = [part for part in [method, parameter_id] if part and part != "nan"]
    if subtitle_parts:
        ax.text(0.0, 1.01, " | ".join(subtitle_parts), transform=ax.transAxes, fontsize=9.8, color=MUTED)

    for idx, text in enumerate(display):
        ax.text(min(plot_values[idx] + 0.025, 1.03), idx, text, va="center", ha="left", fontsize=10.2, color=NAVY, fontweight="bold")
    return save_figure(fig, output_dir, "best_parameter_set")


def write_manifest(output_dir: Path, run_dir: Path, generated: dict[str, list[str]], skipped: dict[str, str]) -> None:
    manifest = {
        "run_dir": str(run_dir.relative_to(PROJECT_ROOT) if run_dir.is_relative_to(PROJECT_ROOT) else run_dir),
        "output_dir": str(output_dir.relative_to(PROJECT_ROOT) if output_dir.is_relative_to(PROJECT_ROOT) else output_dir),
        "generated": generated,
        "skipped": skipped,
    }
    (output_dir / "figure_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def build(args: argparse.Namespace) -> dict[str, list[str]]:
    configure_style()
    latest = load_latest()
    run_dir = resolve_run_dir(args.run_dir, latest)
    output_dir = resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    final_method = read_csv(resolve_input(run_dir, latest, "final_method_comparison_results_csv", "final_method_comparison_results.csv"))
    all_evaluations = read_csv(resolve_input(run_dir, latest, "all_evaluations_csv", "all_evaluations.csv"))
    bo_surrogate = read_csv(resolve_input(run_dir, latest, "table2_bo_surrogate_csv", "table2_bo_surrogate.csv"))
    pareto = read_csv(resolve_input(run_dir, latest, "table3_pareto_csv", "table3_pareto.csv"))

    generated: dict[str, list[str]] = {}
    skipped: dict[str, str] = {}

    evals = normalize_evaluations(final_method, all_evaluations)
    if not evals.empty:
        evals.to_csv(output_dir / "clean_validated_evaluations.csv", index=False)
        valid_points = evals[evals["is_valid"]].copy()
        valid_points.to_csv(output_dir / "clean_valid_points.csv", index=False)
        rounds = build_round_summary(evals)
        rounds.to_csv(output_dir / "clean_method_round_summary.csv", index=False)
        method_count = rounds["method"].nunique() if not rounds.empty else 0
        if method_count >= 2:
            generated["optimization_convergence"] = plot_convergence(rounds, output_dir)
            generated["round_best_score_by_method"] = plot_round_best(rounds, output_dir)
        elif method_count == 1:
            skipped["method_comparison_figures"] = "Only one optimization method is available in this run."
        else:
            skipped["method_comparison_figures"] = "No valid method rounds are available."
    else:
        skipped["method_comparison_figures"] = "No final_method_comparison_results.csv or all_evaluations.csv input is available."

    bo_clean = clean_bo_surrogate(bo_surrogate)
    if not bo_clean.empty:
        bo_clean.to_csv(output_dir / "clean_bo_surrogate_trace.csv", index=False)
        generated["bo_surrogate_trace"] = plot_bo_surrogate(bo_clean, output_dir)
    else:
        skipped["bo_surrogate_trace"] = "No usable table2_bo_surrogate.csv input is available."

    pareto_clean = clean_pareto(pareto)
    if not pareto_clean.empty:
        pareto_clean.to_csv(output_dir / "clean_pareto_tradeoff.csv", index=False)
        generated["emergency_general_delay_tradeoff"] = plot_delay_tradeoff(pareto_clean, output_dir)
    else:
        skipped["emergency_general_delay_tradeoff"] = "No usable table3_pareto.csv input is available."

    best = best_parameter_row(evals, pareto_clean)
    if best is not None:
        pd.DataFrame([best.to_dict()]).to_csv(output_dir / "clean_best_parameter_set.csv", index=False)
        generated["best_parameter_set"] = plot_best_parameter_summary(best, output_dir)
    else:
        skipped["best_parameter_set"] = "No valid score row is available."

    write_manifest(output_dir, run_dir, generated, skipped)
    if skipped:
        print("Skipped:")
        for name, reason in skipped.items():
            print(f"- {name}: {reason}")
    print("Generated:")
    for name, paths in generated.items():
        print(f"- {name}: {', '.join(paths)}")
    return generated


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build final optimization result figures.")
    parser.add_argument("--run-dir", default=None, help="Specific optimization output directory. Defaults to outputs/latest.json.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for PNG/SVG figures and clean CSVs.")
    return parser.parse_args()


def main() -> None:
    build(parse_args())


if __name__ == "__main__":
    main()
