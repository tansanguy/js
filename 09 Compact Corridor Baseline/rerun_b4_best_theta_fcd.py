#!/usr/bin/env python3
"""Re-run B04 baseline + B4 at a given theta with FCD emission for visualization.

The theta BO (run_b4_theta_bo.py) only records the best theta; it does not keep
per-eval FCD by default. This driver reuses the BO module's own validated
evaluation functions (run_b04_task / run_b4_task) to produce fresh fcd.xml for
both the no-control B04 baseline and the B4 controlled run at the chosen theta.

Default theta = best from results/metrics/compact_v9_B4_theta_bo/<run-id>/bo_loop_summary.json.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# The BO module imports `from run_b0_b4_signal_pipeline import ...` and
# `from b4_runtime import ...`, so the package dir must be importable.
sys.path.insert(0, str(HERE))

bo = _load_module("run_b4_theta_bo", HERE / "run_b4_theta_bo.py")
from b4_runtime import B4ThetaParams, B04_MODE, B4_MODE  # noqa: E402
from run_b0_b4_signal_pipeline import B4RunTask, run_b04_task, run_b4_task  # noqa: E402


def load_best_theta(run_id: str) -> dict:
    summ = PROJECT_ROOT / "results/metrics/compact_v9_B4_theta_bo" / run_id / "bo_loop_summary.json"
    data = json.loads(summ.read_text(encoding="utf-8"))
    best = data["best"]
    # Pass through every decision-variable field the summary records. The theta
    # variable set has changed across runs (old: t_lead/tau/ext_max/hold_max/d_up;
    # new: alpha/t_lead/delta_T_thr/G_ext/Q_trig), so we forward whatever is
    # present and let B4ThetaParams.from_row fill the rest with defaults rather
    # than hard-coding a fixed key list.
    theta_keys = (
        "parameter_id", "alpha", "t_lead", "delta_T_thr", "G_ext", "Q_trig",
        "tau", "ext_max", "hold_max", "d_up", "tau_scale", "tau_numerator_gamma",
    )
    return {k: best[k] for k in theta_keys if k in best}


def main() -> int:
    p = argparse.ArgumentParser(description="Re-run B04+B4 at best theta with FCD for viz")
    p.add_argument("--bo-run-id", default="b4_theta_bo_001",
                   help="theta BO run-id to read best theta from")
    p.add_argument("--run-id", default=None,
                   help="output run-id for this viz re-run (default: <bo-run-id>_viz)")
    p.add_argument("--seed", type=int, default=bo.DEFAULT_SEED)
    p.add_argument("--sumo-binary", default=None)
    p.add_argument("--run-root", type=Path, default=PROJECT_ROOT / "runs/final/compact_v9_B4_viz")
    p.add_argument("--hard-max-sim-time", type=float, default=4000.0,
                   help="SUMO force-stop time. Must match the BO run (the S1-forced "
                        "global-reality net needs ~4000s for the EV to arrive; the "
                        "phase default of 1800s makes the B04 baseline fail).")
    args = p.parse_args()

    out_run_id = args.run_id or f"{args.bo_run_id}_viz"
    theta = load_best_theta(args.bo_run_id)
    print(f"best theta ({args.bo_run_id}): {theta}")

    # Build the shared real context (validates static inputs, runs/loads B04 baseline).
    ctx_args = argparse.Namespace(
        phase="bo-smoke", seed=args.seed, sumo_binary=args.sumo_binary,
        emit_fcd=True, emit_tls_states=True, resume=False,
        net_file=bo.B04_NET, background_route=bo.B04_AA_BACKGROUND_ROUTE,
        hard_max_sim_time=args.hard_max_sim_time,
        run_root=args.run_root, metrics_root=PROJECT_ROOT / "results/metrics/compact_v9_B4_viz",
    )
    print("running B04 baseline (no_control) with FCD + TLS state dump ...")
    real_context = bo.prepare_real_context(out_run_id, ctx_args)

    # B4 controlled run at best theta, with FCD + TLS state dump.
    print(f"running B4 controlled run at theta {theta['parameter_id']} with FCD ...")
    b4_dir = args.run_root / out_run_id / B4_MODE / theta["parameter_id"] / "repeat_001"
    task = B4RunTask(out_run_id, B4_MODE, theta["parameter_id"], 1, args.seed, b4_dir)
    b4_row = run_b4_task(
        task,
        real_context["stage1"],
        real_context["phase_config"],
        real_context["free_reference"],
        real_context["free_rows_by_id"],
        args.sumo_binary,
        True,  # emit_fcd
        B4ThetaParams.from_row(theta),
        emit_tls_states=True,
    )

    b04_dir = args.run_root / out_run_id / B04_MODE / "no_control" / "repeat_001"
    print("\n=== DONE ===")
    print(f"B04 fcd: {b04_dir / 'fcd.xml'}")
    print(f"B4  fcd: {b4_dir / 'fcd.xml'}")
    print(f"B4 score_sec: {b4_row.get('score_sec')}  emergency_arrived: {b4_row.get('emergency_arrived')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
