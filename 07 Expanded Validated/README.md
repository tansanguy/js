# Expanded V7 B0 Baseline Pipeline

## Scope

`07 Expanded Validated` is an independent Expanded V7 pipeline for the B0 firetruck baseline. It does not replace the existing final or validated pipelines.

Reality recall reference CSV:

```text
/Users/junlee/Desktop/js/toegye_ro_mainstream_segments_english.csv
```

The current recommended B0 baseline is the conservative B0 manifest:

```text
configs/expanded_v7_conservative_b0_manifest.json
```

This manifest uses the make-sense fixed net:

```text
data_prepared/expanded_v7/net/jungbu_expanded_v7_passenger_lanes_repaired_tls_fixed_release_route_overopen_metered_release_fixed_lane_drop_fixed_plausibility_overopen_make_sense_fixed.net.xml
```

The older aggressive V7 manifest is kept separate:

```text
configs/expanded_v7_b0_manifest.json
```

Use the conservative manifest as the B0 reality baseline unless the experiment explicitly needs the earlier aggressive firetruck behavior.

## What "Make Sense" Means

The goal is not perfect OSM reconstruction or 100% vehicle-count recall. The goal is a simulation baseline that is structurally plausible:

- no broken protected route pairs,
- no high-risk `3 lanes -> 1 lane` or `2 lanes -> 1 lane` drops on protected/high-flow continuation,
- no effectively impassable protected/high-flow edges caused by impossible road geometry,
- Toegye-ro lane recall remains acceptable,
- B0 firetruck driving is conservative enough that it does not visually appear to push through vehicles.

Ordinary side-street 1-lane edges, short OSM edges, and short greens are not automatically treated as errors. They are high-risk only when they sit on Toegye-ro, the accepted firetruck route, or high-flow continuation and create unrealistic stop-flow.

## Main Pipeline

Run steps from the repository root:

```bash
.venv/bin/python "07 Expanded Validated/step01_define_expanded_area.py"
.venv/bin/python "07 Expanded Validated/step02_build_expanded_net.py"
.venv/bin/python "07 Expanded Validated/step03_build_expanded_toegye_mapping.py"
.venv/bin/python "07 Expanded Validated/step04_repair_expanded_lanes.py"
.venv/bin/python "07 Expanded Validated/step05_build_firetruck_start_and_route_review.py"
.venv/bin/python "07 Expanded Validated/step06_apply_firetruck_route_acceptance.py"
.venv/bin/python "07 Expanded Validated/step07_build_expanded_demand.py"
```

Then build and audit the road network candidates:

```bash
.venv/bin/python "07 Expanded Validated/expanded_v7_pipeline.py" tls_fix
.venv/bin/python "07 Expanded Validated/expanded_v7_pipeline.py" route_edge_overopen_metering
.venv/bin/python "07 Expanded Validated/expanded_v7_pipeline.py" release_junction_fixed
.venv/bin/python "07 Expanded Validated/expanded_v7_pipeline.py" lane_drop_fixed
.venv/bin/python "07 Expanded Validated/expanded_v7_pipeline.py" plausibility_overopen_speedcap
.venv/bin/python "07 Expanded Validated/expanded_v7_pipeline.py" make_sense_audit
.venv/bin/python "07 Expanded Validated/expanded_v7_pipeline.py" make_sense_candidate
```

The make-sense candidate is adopted for conservative B0 only when its post-audit passes:

- `structural_defect_count = 0`
- `lane_drop_3_to_1_count = 0`
- `lane_drop_2_to_1_count = 0`
- `disconnected_pair_count = 0`
- SUMO net load `PASS`
- firetruck route connectivity `PASS`

The adoption evidence is stored in:

```text
data_prepared/expanded_v7/net/make_sense_net_candidate_summary.json
```

## Conservative B0 Baseline

Build the conservative route XML and manifest:

```bash
.venv/bin/python "07 Expanded Validated/expanded_v7_pipeline.py" conservative_manifest
```

Run B0 with the make-sense fixed net and conservative firetruck profile:

```bash
.venv/bin/python "07 Expanded Validated/expanded_v7_pipeline.py" run_conservative_b0
```

This calls the existing experiment runner with:

- `--modes B0`
- `--repeats 1`
- `--workers 1`
- `--emit-fcd`
- output prefix `expanded_v7_conservative_b0`

The firetruck profile is intentionally conservative:

- `vClass="emergency"` and `guiShape="emergency"` are preserved,
- no B0 blue-light/TLS priority,
- no `insertionChecks="none"`,
- scheduled depart at `600s`,
- forced lane guidance disabled,
- lower lane-change assertiveness and higher cooperation.

Expected latest output pointer:

```text
results/metrics/expanded_v7_conservative_b0/latest.json
```

## Validation And Audits

Reality validation:

```bash
.venv/bin/python "07 Expanded Validated/expanded_v7_pipeline.py" validate \
  --results-csv auto
```

Near-conflict audit for conservative B0:

```bash
.venv/bin/python "07 Expanded Validated/expanded_v7_pipeline.py" conservative_conflict_audit
```

The near-conflict audit checks whether the firetruck and background vehicles are too close in the same lane in FCD, because collision count alone can miss the visual impression of "pushing through" traffic.

Expected latest output pointer:

```text
results/metrics/expanded_v7_conservative_b0_conflict_audit/latest.json
```

## Main-Flow Visualization

Do not edit `04_visualize` for Expanded V7 custom visualization. Use the wrapper under `04-2 Visualize`:

```bash
.venv/bin/python "04-2 Visualize/visualize_expanded_v7_b0_main_flow.py" \
  --latest-json results/metrics/expanded_v7_conservative_b0/latest.json \
  --manifest configs/expanded_v7_conservative_b0_manifest.json \
  --output-stem expanded_v7_conservative_b0_main_flow
```

Expected outputs:

```text
results/html/expanded_v7_conservative_b0_main_flow_index.html
results/html/expanded_v7_conservative_b0_main_flow_animation.html
results/html/expanded_v7_conservative_b0_main_flow_animation.json
```

## Current Accepted Result

Latest accepted conservative B0 run:

```text
run_id: 20260603T171418_439591Z0000
```

Summary:

- `sumo_exit_code = 0`
- `route_error_count = 0`
- `emergency_arrived = True`
- `emergency_teleport = False`
- collisions `0`
- background teleports `0`
- remaining background vehicles `199`
- emergency travel time `625s`
- same-lane near-conflict count `0`
- minimum same-lane distance `7.501m`

Interpretation:

The make-sense fixed net is acceptable for the conservative B0 reality baseline. Remaining low-speed or free-flow mismatches should be interpreted as demand, TLS, or boundary calibration issues unless a future make-sense audit finds a new structural defect.

## Regression Checks

Run:

```bash
.venv/bin/python -m unittest discover -s tests
git diff -- 04_visualize
```

Expected:

- all unit tests pass,
- `git diff -- 04_visualize` is empty.

