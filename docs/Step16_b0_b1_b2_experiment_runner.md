# Step 16 B0/B1/B2 Experiment Runner

## Purpose

`02_simulation/run_b0_b1_b2_experiment.py` is the single entrypoint for final B0/B1/B2 route-level experiments.

Earlier Step9-Step15 scripts are setup, audit, smoke, and diagnostic tools. Do not use them as the final experiment command.

## Fixed Final Inputs

Use `configs/final_experiment_manifest.json`.

- net: `data_prepared/net/jungbu_ellipse_passenger.net.xml`
- background: `data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml`
- route set: `b0_valid_18`
- excluded route: `ER_ACC_013`
- TLS audit: `data_prepared/signals/tls_phase_audit_spine_v2.csv`
- B1 config: `configs/b1_priority_signal_config.json`
- B2 parameter sets: `configs/b2_parameter_sets.csv`

Teleport policy is unchanged:

- emergency vehicle teleport = FAIL
- background/general vehicle teleport = WARNING

## B0/B1/B2 Meaning

- B0: no signal control.
- B1: default Green Wave v1 config.
- B2: parameterized Green Wave v1 using team-provided rows in `configs/b2_parameter_sets.csv`.

B2 is not Bayesian Optimization. This repository runs parameter sets and exports metrics for later analysis.

## Commands

Small manifest preflight:

```bash
python3 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --modes B0 B1 B2 \
  --routes ER_ACC_002 ER_ACC_019 \
  --repeats 1 \
  --workers 2 \
  --output-prefix preflight_manifest
```

Main 18-route run:

```bash
python3 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --modes B0 B1 B2 \
  --route-set b0_valid_18 \
  --repeats 5 \
  --workers 4 \
  --output-prefix final_18route
```

## Output Policy

Outputs are prefix-scoped and do not overwrite preflight/final runs unless the same `--output-prefix` is reused:

- `results/metrics/{output_prefix}_b0_b1_b2_summary.csv`
- `results/metrics/{output_prefix}_b0_b1_b2_summary.json`
- `results/metrics/{output_prefix}_signal_events.csv`
- `results/metrics/{output_prefix}_compare_by_route.csv`
- `runs/final/{mode}/{parameter_id}/{repeat_id}/{route_id}/`

Legacy generic `experiment_*.csv/json` names are only available with `--legacy-output-names`.

## Guardrails

The runner blocks unsafe final-style execution when:

- manifest paths are missing
- background route does not contain `scale_0p15`
- `ER_ACC_013` appears in `b0_valid_18`
- B2 parameter CSV is empty or lacks required columns

Use `--allow-nonfinal-background` only for explicit diagnostics, not final comparison.

## Runtime Estimate

Observed small preflight runs are seconds per route-mode run. For 18 routes x 3 modes x 5 repeats = 270 runs:

- `--workers 4`: roughly 8-20 minutes
- `--workers 8`: roughly 5-12 minutes, after stability check

If B2 has more parameter rows, runtime grows linearly.

## Current Preflight

Manifest preflight should be run before final. It must show:

- route list excludes `ER_ACC_013`
- background path is the 0.15x route
- emergency teleport is false for all successful runs
- background teleport, if any, is only a warning
