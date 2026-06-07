# B4 Optimization S1-forced

This folder contains the fixed-budget optimizer comparison for the current
S1-forced B04/B4 input bundle.

Canonical inputs:

- Net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_s1forced.net.xml`
- Demand: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_ad_stage23_trigger.rou.xml`
- Stage1: `data_prepared/compact_v9/b4_stage1_s1forced`
- Signal timing provenance: `global_reality_signal_profiles.csv` and `global_tls_a008_itst_mapping.csv`

Default experiment:

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py"
```

The default run uses `n=15`, `m=50`, and compares Random Search, CMA-ES, and BO
under the same fixed-budget policy. One round means one evaluated theta.

Fast contract check without SUMO:

```bash
python "09-1 B4 Optimization S1forced/run_b4_optimization_s1forced.py" \
  --mock-eval \
  --run-id mock_contract \
  --n 2 \
  --m 5 \
  --bo-initial 2
```

Main outputs under `outputs/{run_id}/`:

- `table1_best_so_far.csv`
- `table2_bo_surrogate.csv`
- `table3_pareto.csv`
- `figure1_best_so_far.png`
- `figure2_bo_surrogate.png`
- `figure3_pareto.png`
- `noise_check_5repeat.csv`
- `experiment_summary.json`
