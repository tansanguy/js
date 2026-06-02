# 01-2 Validated Pipeline

This folder builds a separate validated input set without replacing the final baseline inputs.

Pipeline order:

```bash
python "01-2 Validated/step01_build_toegye_edge_mapping.py"
python "01-2 Validated/step02_build_lane_overrides.py"
python "01-2 Validated/step03_rebuild_lane_repaired_net.py"
python "01-2 Validated/step04_validate_repaired_map.py"
python "01-2 Validated/step05_build_demand_scale_variants.py"
python "01-2 Validated/step06_run_b0_scale_sweep.py" --max-variants 1
python "01-2 Validated/step07_select_validated_inputs.py"
```

The original final inputs stay unchanged. Validated artifacts are written under:

- `data_prepared/validated/`
- `results/metrics/validated_*`
- `configs/validated_experiment_manifest.json`

The intended workflow is lane repair first, then demand scale calibration on the repaired net.
