# Final Experiment Runbook

## One Correct Entrypoint

Use only:

```bash
python3 02_simulation/run_b0_b1_b2_experiment.py --manifest configs/final_experiment_manifest.json ...
```

Step9-Step15 scripts are retained for setup, audit, smoke, diagnosis, and explanation. They are not the final experiment command.

## Teleport Policy

- Emergency vehicle teleport: FAIL.
- Background/general vehicle teleport: WARNING.
- Do not hide emergency teleport by only increasing `time-to-teleport`.

## Preflight Command

```bash
python3 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --modes B0 B1 B2 \
  --routes ER_ACC_002 ER_ACC_019 \
  --repeats 1 \
  --workers 2 \
  --output-prefix preflight_manifest
```

Expected outputs:

- `results/metrics/preflight_manifest_experiment_results.csv`
- `results/metrics/preflight_manifest_experiment_summary.json`
- `runs/final/preflight_manifest/...`

## Final 18-Route Command

```bash
python3 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --modes B0 B1 B2 \
  --route-set b0_valid_18 \
  --repeats 5 \
  --workers 4 \
  --output-prefix final_18route
```

## B2 Parameter CSV

Edit `configs/b2_parameter_sets.csv`.

Required columns:

```csv
parameter_id,D_det,alpha,t_lead,G_ext,tau,fallback_v_e_mps,rho
```

B2 is parameter-set execution, not Bayesian Optimization.

## Success Criteria

For each run:

- `sumo_exit_code=0`
- `emergency_departed=True`
- `emergency_arrived=True`
- `emergency_teleport=False`
- `route_error_count=0`

Background teleport can produce WARNING. Emergency teleport produces FAIL.

## Output Policy

Every non-legacy experiment writes one report CSV and one summary JSON:

- `results/metrics/{output_prefix}_experiment_results.csv`
- `results/metrics/{output_prefix}_experiment_summary.json`

Raw SUMO outputs are also prefix-scoped:

- `runs/final/{output_prefix}/{mode}/{parameter_id}/{repeat_id}/{route_id}/`

Generic `experiment_*` outputs are legacy only. Use them only with `--legacy-output-names`; do not use them for final comparison.

## Current Fixed Inputs

Defined in `configs/final_experiment_manifest.json`:

- reduced active net
- 0.15x background route
- spine-v2 emergency route CSV
- B0-valid 18 route set
- `ER_ACC_013` excluded
- TLS audit and B1/B2 config inputs
