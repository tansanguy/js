# Final Experiment Runbook

## One Entrypoint

Use only:

```bash
python3 02_simulation/run_b0_b1_b2_experiment.py --manifest configs/final_experiment_manifest.json ...
```

Step9-Step15 scripts are retained for setup, audit, smoke, diagnosis, and explanation. They are not the final experiment command.

## Modes

- `B00`: emergency-only free-flow run. No background vehicles, no signal control.
- `B0`: peak background demand plus one emergency vehicle. No signal control.
- `B2`: same peak background demand as B0 plus corridor priority control.

`B1` is excluded from final comparison.

## Pipeline 1: Parameter Input Simulation

This run uses the single fire-station-to-Seoul-Station route. It is the future Bayesian Optimization input surface, but this repository only executes parameter rows.

```bash
python3 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline parameter_input_sim \
  --modes B00 B0 B2 \
  --repeats 1 \
  --workers 1
```

Output:

- `results/metrics/parameter_input_sim.csv`
- raw SUMO files under `runs/final/parameter_input_sim/...`

The synthetic route is generated from fire-station edge `-381802881#2` to Seoul Station edge `438360331#2`.

## Pipeline 2: Final Effect Validation

This run uses the existing `b0_valid_18` route set and keeps `ER_ACC_013` excluded.

```bash
python3 02_simulation/run_b0_b1_b2_experiment.py \
  --manifest configs/final_experiment_manifest.json \
  --pipeline final_effect_validation_sim \
  --modes B00 B0 B2 \
  --repeats 5 \
  --workers 4
```

Output:

- `results/metrics/final_effect_validation_sim.csv`
- raw SUMO files under `runs/final/final_effect_validation_sim/...`

## B2 Parameter CSV

Edit `configs/b2_parameter_sets.csv`.

Required columns:

```csv
parameter_id,D_det,alpha,G_ext
```

External optimizers can generate the same CSV and reuse the runner. The runner does not implement Bayesian Optimization.

## CSV Metrics

Each pipeline writes one CSV. Core columns:

- `pipeline,mode,parameter_id,repeat_id,route_id`
- `emergency_travel_time_sec,b00_emergency_travel_time_sec,A_delay_sec,N_delay_sec,T_recovery_sec,score_sec`
- `emergency_arrived,emergency_teleport,background_vehicle_count,final_status,warning_reason,failure_reason,run_dir`

Metric definitions:

- `A_delay_sec`: B0/B2 emergency travel time minus same route/repeat B00 emergency travel time. B00 is `0.00`.
- `N_delay_sec`: average mainstream general-vehicle delay, measured from TraCI vehicle time on corridor edges against edge free-flow time.
- `T_recovery_sec`: B2 maximum time from emergency passing a controlled mainstream signal to restoring its normal program. B00/B0 are `0.00`.
- `score_sec`: equal-weight sum of `A_delay_sec`, `N_delay_sec`, and `T_recovery_sec`.

All `_sec` fields are seconds rounded to two decimals.

## Success Criteria

- `sumo_exit_code=0`
- `emergency_departed=True`
- `emergency_arrived=True`
- `emergency_teleport=False`
- `route_error_count=0`
- `B00`, `B0`, and `B2` rows are present in the same pipeline CSV

Background teleport can produce WARNING. Emergency teleport produces FAIL.
