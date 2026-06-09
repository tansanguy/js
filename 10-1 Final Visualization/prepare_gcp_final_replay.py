#!/usr/bin/env python3
"""Prepare measured replay artifacts from a completed GCP final run.

This script is intentionally scoped to 10-1.  It does not change the simulator.
It connects to the GCP VM, pulls final route summaries, selects the best theta
and repeat under strict replay criteria, and writes the remote command for a
single fixed-depart measured rerun with FCD/TLS output enabled.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
THIS_DIR = Path(__file__).resolve().parent
DEFAULT_GCLOUD = Path("/opt/homebrew/share/google-cloud-sdk/bin/gcloud")
DEFAULT_CLOUDSDK_CONFIG = PROJECT_ROOT / ".gcloud"
DEFAULT_INSTANCE = "instance-20260608-125207"
DEFAULT_ZONE = "us-central1-a"
DEFAULT_REMOTE_ROOT = "/home/junlee/js"
DEFAULT_RUN_ID = "gcp_bo_top5_dongho_commonend_splitN_20260609_065501"
DEFAULT_ROUTE_ID = "FINAL_DEST_DONGHO_001"
DEFAULT_STAGING = THIS_DIR / "gcp_final_replay_staging"
DEFAULT_THETA_CSV = (
    "results/metrics/compact_v9_final_destination_validation/"
    "gcp_bo_top5_dongho_final30_recovery_20260609_0514/"
    "robust_selection/selected_for_final_theta_candidates.csv"
)
DEFAULT_EXACT_ROUTE_JSON = "/home/junlee/js/tmp_codex_sync_post600_20260609_151458/dongho_exact_route.json"
DEFAULT_NET = (
    "09 Compact Corridor Baseline/tdata_signal/nets/"
    "jungbu_compact_v9_B04_global_reality_s1forced.net.sumo_compat.net.xml"
)
DEFAULT_BACKGROUND = "data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml"
DEFAULT_STAGE1 = "data_prepared/compact_v9/b4_stage1_s1forced"


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def safe_float(value: Any, default: float = math.inf) -> float:
    if value in {None, ""}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pass"}


def repeat_number(value: Any) -> int:
    match = re.search(r"(\d+)$", str(value))
    return int(match.group(1)) if match else 0


def repeat_dir(number: int) -> str:
    return f"repeat_{number:03d}"


def token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def run(cmd: list[str], *, dry_run: bool = False, capture: bool = False) -> str:
    print("$ " + " ".join(cmd))
    if dry_run:
        return ""
    if capture:
        return subprocess.check_output(cmd, cwd=PROJECT_ROOT, text=True)
    subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)
    return ""


@dataclass(frozen=True)
class Remote:
    gcloud: Path
    cloud_config: Path
    instance: str
    zone: str
    remote_root: str

    def base(self) -> list[str]:
        return [
            "env",
            f"CLOUDSDK_CONFIG={self.cloud_config}",
            str(self.gcloud),
        ]

    def ssh(self, command: str) -> list[str]:
        return self.base() + [
            "compute",
            "ssh",
            self.instance,
            "--zone",
            self.zone,
            "--command",
            command,
        ]

    def scp_from(self, remote_path: str, local_path: Path) -> list[str]:
        return self.base() + [
            "compute",
            "scp",
            "--recurse",
            f"{self.instance}:{self.remote_root}/{remote_path}",
            str(local_path),
            "--zone",
            self.zone,
        ]


@dataclass
class Pair:
    theta_label: str
    theta_dir: str
    repeat: int
    b04: dict[str, str]
    b4: dict[str, str]

    @property
    def objective(self) -> float:
        return safe_float(self.b4.get("objective_score"))

    @property
    def de(self) -> float:
        return safe_float(self.b4.get("D_E_sec"))

    @property
    def dg(self) -> float:
        return safe_float(self.b4.get("D_G_sec"))


def valid_mode(row: dict[str, str]) -> bool:
    return (
        row.get("final_status") == "PASS"
        and row.get("failed") == "False"
        and truthy(row.get("emergency_arrived"))
        and not truthy(row.get("emergency_teleport"))
        and str(row.get("background_teleported", "0")) in {"", "0"}
    )


def paired_valid_rows(
    rows: list[dict[str, str]],
    theta_label: str,
    theta_dir: str,
    *,
    require_stage2: bool,
) -> list[Pair]:
    by_repeat: dict[int, dict[str, dict[str, str]]] = {}
    for row in rows:
        number = repeat_number(row.get("repeat_id"))
        if number <= 0:
            continue
        by_repeat.setdefault(number, {})[row.get("mode", "")] = row

    pairs: list[Pair] = []
    for number, modes in sorted(by_repeat.items()):
        b04 = modes.get("B04")
        b4 = modes.get("B4")
        if not b04 or not b4:
            continue
        if not valid_mode(b04) or not valid_mode(b4):
            continue
        if safe_float(b4.get("D_E_sec")) > safe_float(b04.get("D_E_sec")):
            continue
        if safe_float(b4.get("stage3_preemption_count"), 0.0) <= 0:
            continue
        if safe_float(b4.get("signal_event_count"), 0.0) <= 0:
            continue
        if require_stage2 and safe_float(b4.get("stage2_hold_count"), 0.0) <= 0:
            continue
        pairs.append(Pair(theta_label, theta_dir, number, b04, b4))
    return pairs


def theta_label_from_path(path: Path) -> str:
    parts = path.parts
    for part in parts:
        if part.startswith("top_"):
            return part
    return path.parent.name


def find_local_route_csvs(staging: Path, run_id: str, route_id: str) -> list[Path]:
    root = staging / "metrics" / run_id
    return sorted(root.glob(f"robust_final/top_*/final/{route_id}/route_runs.csv"))


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else math.inf


def select_best(args: argparse.Namespace) -> tuple[Pair, dict[str, Any]]:
    theta_summaries: list[dict[str, Any]] = []
    all_pairs: dict[str, list[Pair]] = {}
    display_pairs: dict[str, list[Pair]] = {}
    csv_paths = find_local_route_csvs(args.staging, args.run_id, args.route_id)
    for csv_path in csv_paths:
        theta_label = theta_label_from_path(csv_path)
        theta_dir = str(csv_path.parent.relative_to(args.staging / "metrics" / args.run_id))
        rows = read_csv(csv_path)
        strict_pairs = paired_valid_rows(
            rows,
            theta_label,
            theta_dir,
            require_stage2=False,
        )
        visible_pairs = paired_valid_rows(
            rows,
            theta_label,
            theta_dir,
            require_stage2=args.require_stage2,
        )
        all_pairs[theta_label] = strict_pairs
        display_pairs[theta_label] = visible_pairs
        if not strict_pairs:
            theta_summaries.append(
                {
                    "theta_label": theta_label,
                    "theta_dir": theta_dir,
                    "valid_pairs": 0,
                    "display_valid_pairs": 0,
                }
            )
            continue
        mean_objective = mean([pair.objective for pair in strict_pairs])
        mean_de = mean([pair.de for pair in strict_pairs])
        best_pair = sorted(strict_pairs, key=lambda pair: (pair.objective, pair.de, pair.dg, pair.repeat))[0]
        best_display_pair = (
            sorted(visible_pairs, key=lambda pair: (pair.objective, pair.de, pair.dg, pair.repeat))[0]
            if visible_pairs
            else None
        )
        theta_summaries.append(
            {
                "theta_label": theta_label,
                "theta_dir": theta_dir,
                "valid_pairs": len(strict_pairs),
                "display_valid_pairs": len(visible_pairs),
                "mean_objective": round(mean_objective, 6),
                "mean_D_E_sec": round(mean_de, 6),
                "best_repeat": best_pair.repeat,
                "best_repeat_objective": round(best_pair.objective, 6),
                "best_repeat_D_E_sec": round(best_pair.de, 6),
                "best_display_repeat": best_display_pair.repeat if best_display_pair else "",
                "best_display_objective": round(best_display_pair.objective, 6) if best_display_pair else "",
                "best_display_D_E_sec": round(best_display_pair.de, 6) if best_display_pair else "",
            }
        )

    if not any(all_pairs.values()):
        raise SystemExit("no valid B04/B4 pairs found in pulled final summaries")

    theta_summaries.sort(key=lambda item: (item.get("mean_objective", math.inf), item["theta_label"]))
    best_theta = theta_summaries[0]["theta_label"]
    theta_pairs = display_pairs[best_theta] if args.require_stage2 else all_pairs[best_theta]
    if not theta_pairs:
        raise SystemExit(
            f"best theta {best_theta} has no repeat satisfying the display constraints; "
            "rerun without --require-stage2 or change the display criterion"
        )
    selected = sorted(theta_pairs, key=lambda pair: (pair.objective, pair.de, pair.dg, pair.repeat))[0]
    return selected, {
        "theta_summaries": theta_summaries,
        "found_theta_count": len(csv_paths),
        "expected_theta_count": args.expected_theta_count,
        "ready_for_final": len(csv_paths) >= args.expected_theta_count,
        "require_stage2": args.require_stage2,
        "best_theta": best_theta,
    }


def remote_find_route_csvs(args: argparse.Namespace, remote: Remote) -> list[str]:
    command = (
        f"cd {remote.remote_root} && "
        f"find results/metrics/compact_v9_final_destination_validation/{args.run_id}/robust_final "
        f"-path '*/final/{args.route_id}/route_runs.csv' -type f | sort"
    )
    output = run(remote.ssh(command), capture=True)
    return [line.strip() for line in output.splitlines() if line.strip().endswith("route_runs.csv")]


def pull_route_csvs(args: argparse.Namespace, remote: Remote, remote_csvs: list[str]) -> None:
    for remote_csv in remote_csvs:
        local = args.staging / "metrics" / args.run_id / Path(remote_csv).relative_to(
            f"results/metrics/compact_v9_final_destination_validation/{args.run_id}"
        )
        local.parent.mkdir(parents=True, exist_ok=True)
        run(remote.scp_from(remote_csv, local.parent))


def repeat_paths(pair: Pair, args: argparse.Namespace) -> dict[str, str]:
    final_root = (
        f"runs/compact_v9_final_destination_validation/{args.run_id}/"
        f"{pair.theta_dir}"
    )
    # theta_dir is robust_final/top_x/final/ROUTE.  The run tree mirrors it.
    b04 = f"{final_root}/B04/no_control/{repeat_dir(pair.repeat)}"
    b4 = f"{final_root}/B4/{pair.b4.get('parameter_id')}/{repeat_dir(pair.repeat)}"
    route_xml = f"{final_root}/routes/firetruck_depart_{pair.repeat:03d}.rou.xml"
    return {"B04": b04, "B4": b4, "route_xml": route_xml}


def build_remote_measured_command(pair: Pair, args: argparse.Namespace) -> str:
    depart = safe_float(pair.b4.get("ev_depart_sec"), safe_float(pair.b4.get("emergency_depart"), 600.0))
    measured_run_id = args.measured_run_id or (
        f"10_1_measured_{token(args.run_id)}_{token(pair.theta_label)}_{repeat_dir(pair.repeat)}"
    )
    parts = [
        "cd /home/junlee/js",
        "&&",
        "python3",
        '"10 Final Destination Validation/final_destination_validation.py"',
        "--validation-mode robust-theta-selection",
        "--phase final",
        f'--net "{args.net}"',
        f'--background-route "{args.background_route}"',
        f'--base-stage1-dir "{args.base_stage1_dir}"',
        "--skip-active-inputs-audit",
        f'--exact-route-json "{args.exact_route_json}"',
        f"--exact-route-id {args.route_id}",
        f"--selected-routes {args.route_id}",
        f'--robust-final-theta-csv "{args.robust_final_theta_csv}"',
        "--robust-mini-batch-repeats 0",
        "--robust-final-top-k 1",
        "--robust-survivor-count 1",
        "--repeats 1",
        "--pilot-repeats 2",
        "--disable-adaptive-repeats",
        "--workers 1",
        "--robust-theta-workers 1",
        "--robust-repeat-workers 1",
        "--hard-max-sim-time 4200",
        "--post-arrival-measure-sec 600",
        "--common-paired-end",
        f"--depart-min {depart:.3f}",
        f"--depart-max {depart:.3f}",
        "--emit-fcd",
        "--emit-tls-states",
        f"--run-id {measured_run_id}",
    ]
    return " ".join(parts)


def write_plan(pair: Pair, args: argparse.Namespace, extra: dict[str, Any]) -> Path:
    paths = repeat_paths(pair, args)
    plan_path = args.output.resolve()
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    measured_command = build_remote_measured_command(pair, args)
    plan = {
        "schema": "10-1_gcp_final_replay_plan.v1",
        "gcp": {
            "instance": args.instance,
            "zone": args.zone,
            "cloudsdk_config": rel(args.cloudsdk_config),
            "remote_root": args.remote_root,
        },
        "source": {
            "run_id": args.run_id,
            "route_id": args.route_id,
            "theta_label": pair.theta_label,
            "theta_dir": pair.theta_dir,
            "repeat_id": repeat_dir(pair.repeat),
        },
        "selection": {
            "rule": (
                "best theta by mean B4 objective among strict valid pairs; "
                "best repeat by B4 objective inside that theta"
                + ("; stage2_hold_count > 0 required" if args.require_stage2 else "")
            ),
            "B04": metric_snapshot(pair.b04),
            "B4": metric_snapshot(pair.b4),
            "improvement_D_E_sec": round(safe_float(pair.b04.get("D_E_sec")) - pair.de, 6),
            "theta_summaries": extra["theta_summaries"],
            "found_theta_count": extra["found_theta_count"],
            "expected_theta_count": extra["expected_theta_count"],
            "ready_for_final": extra["ready_for_final"],
            "best_theta": extra["best_theta"],
            "repeat_selection_scope": "within_best_theta_only",
        },
        "remote_artifacts": paths,
        "commands": {
            "remote_measured_rerun": measured_command,
            "start_remote_measured_rerun": (
                f"tmux new-session -d -s {token(args.measured_run_id or '10_1_measured_replay')} "
                f"'{measured_command} 2>&1 | tee logs/{token(args.measured_run_id or '10_1_measured_replay')}.log'"
            ),
        },
        "contract": {
            "simulation_truth": "selected final run metrics, signal_events.csv, fixed-depart measured rerun FCD, tls_states.csv",
            "visual_smoothing": "allowed only after measured samples are produced",
            "browser_traffic_decision_logic": "forbidden",
            "same_departure_time": True,
            "same_route": True,
        },
    }
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {plan_path}")
    print(json.dumps(plan["selection"], ensure_ascii=False, indent=2))
    return plan_path


def metric_snapshot(row: dict[str, str]) -> dict[str, Any]:
    keys = [
        "mode",
        "repeat_id",
        "ev_depart_sec",
        "final_status",
        "failed",
        "emergency_arrived",
        "emergency_teleport",
        "background_teleported",
        "D_E_sec",
        "D_G_sec",
        "objective_score",
        "signal_event_count",
        "stage2_hold_count",
        "stage3_preemption_count",
        "signal_events_csv",
    ]
    return {key: row.get(key, "") for key in keys}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare GCP final measured replay for 10-1.")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--route-id", default=DEFAULT_ROUTE_ID)
    parser.add_argument("--instance", default=DEFAULT_INSTANCE)
    parser.add_argument("--zone", default=DEFAULT_ZONE)
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--gcloud", type=Path, default=DEFAULT_GCLOUD)
    parser.add_argument("--cloudsdk-config", type=Path, default=DEFAULT_CLOUDSDK_CONFIG)
    parser.add_argument("--staging", type=Path, default=DEFAULT_STAGING)
    parser.add_argument("--output", type=Path, default=THIS_DIR / "10-1_gcp_final_replay_plan.json")
    parser.add_argument("--pull-selected", action="store_true")
    parser.add_argument("--start-remote-measured-rerun", action="store_true")
    parser.add_argument("--expected-theta-count", type=int, default=5)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument(
        "--require-stage2",
        action="store_true",
        help="Only select repeats where Stage2 hold/ALL RED is actually present.",
    )
    parser.add_argument("--measured-run-id", default="")
    parser.add_argument("--robust-final-theta-csv", default=DEFAULT_THETA_CSV)
    parser.add_argument("--exact-route-json", default=DEFAULT_EXACT_ROUTE_JSON)
    parser.add_argument("--net", default=DEFAULT_NET)
    parser.add_argument("--background-route", default=DEFAULT_BACKGROUND)
    parser.add_argument("--base-stage1-dir", default=DEFAULT_STAGE1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    remote = Remote(
        gcloud=args.gcloud.resolve(),
        cloud_config=args.cloudsdk_config.resolve(),
        instance=args.instance,
        zone=args.zone,
        remote_root=args.remote_root.rstrip("/"),
    )
    if not remote.gcloud.is_file():
        raise SystemExit(f"missing gcloud: {remote.gcloud}")
    if not remote.cloud_config.exists():
        raise SystemExit(f"missing Cloud SDK config: {remote.cloud_config}")

    args.staging.mkdir(parents=True, exist_ok=True)
    remote_csvs = remote_find_route_csvs(args, remote)
    if not remote_csvs:
        raise SystemExit(f"no final route_runs.csv found for run_id={args.run_id}")
    print(f"Found {len(remote_csvs)} remote route summaries.")
    if len(remote_csvs) < args.expected_theta_count:
        message = f"final run is not complete yet: {len(remote_csvs)}/{args.expected_theta_count} theta summaries"
        if args.require_complete:
            raise SystemExit(message)
        print("WARNING:", message, file=sys.stderr)
    pull_route_csvs(args, remote, remote_csvs)
    selected, extra = select_best(args)
    plan_path = write_plan(selected, args, extra)

    if args.pull_selected:
        local_artifacts = args.staging / "selected_artifacts" / args.run_id / selected.theta_label / repeat_dir(selected.repeat)
        local_artifacts.mkdir(parents=True, exist_ok=True)
        for remote_path in repeat_paths(selected, args).values():
            run(remote.scp_from(remote_path, local_artifacts))

    if args.start_remote_measured_rerun:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        run(remote.ssh(plan["commands"]["start_remote_measured_rerun"]))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
