# Step 12 Signal Mapping and Net Repair

## 1. Step 10/11 Status

- B0 19-route: `{'PASS': 9, 'WARNING': 9, 'FAIL': 1}`
- B0 failed route: `ER_ACC_013`
- Step 11A TLS audit: `net XML connection/tl/linkIndex/tlLogic via Step 11A TLS audit`
- Step 11B ER_ACC_002 smoke: PASS in prior step

## 2. ER_ACC_013 Diagnosis and Repair Decision

ER_ACC_013 teleported on `:cluster_11277565408_11277565409_414685823_5851251280_9_0` at `1382.0`. The lane is an internal lane for `619147735#4` -> `198564929#0` at TLS `cluster_11277565408_11277565409_414685823_5851251280`. The net connection exists and the lane does not disallow emergency/passenger classes. Emergency-only verification passed, so the preliminary decision is `EXCLUDE_PRELIMINARY` rather than rewriting the net.

## 3. Terminal Candidate Mapping

Terminal candidates are grouped from Step 11A route-TLS audit rows. Controllability is based on net XML `connection`, `tl`, `linkIndex`, and `tlLogic`; GeoJSON is not used for control decisions.

- terminal status counts: `{'PASS': 20, 'WARNING': 0, 'FAIL': 0}`
- covered route count: `19`

## 4. B1 Priority Config

- config: `configs/b1_priority_signal_config.json`
- D_det: `300` detection distance, recorded for approach detection.
- v_e_policy: `current_speed_with_fallback` uses current speed with fallback for ETA.
- fallback_v_e_mps: `8.33`.
- alpha: `1.2` ETA safety margin, recorded for later tuning.
- G_ext: `30` max green extension.
- rho: `restore_original_program` restoration policy.
- SC: `None` shared cycle, record-only now.
- tau: `1` ETA recalculation interval.
- t_lead: `30` lead time before arrival.
- metric_sample_interval: `10` record-only metric cadence.

B2 and Bayesian Optimization are not implemented because this stage only prepares a safe B1 smoke-ready signal network and validates preliminary controller behavior.

## 5. B1 Route-Level Smoke Scope

Included routes are B0-valid routes only. ER_ACC_013 remains excluded unless a documented patched variant is explicitly selected. B1 run status: `RUN`.

## 6. Remaining Blockers and Next Safe Step

Remaining blocker: ER_ACC_013 B0 baseline is invalid under 0.15x background demand. Next safe step is to review ER_ACC_013 local junction demand/signal behavior or run B1 comparison on the B0-valid preliminary route set only.
