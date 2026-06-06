# B04/B4 Algorithm Review and Init20 Validation

- generated_at: 2026-06-05T14:52:39.677872+00:00
- active demand: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml`
- active net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- tau_scale: `0.85`

## Validation Gates

- B04 u130: status=PASS, EV=972.0 sec, speed=15.084 km/h, teleport=False
- B4 u130 tau085 smoke: status=PASS, EV=660.0 sec, teleport=False
- Init20: 8 PASS / 12 FAIL; emergency_stuck=12

## Preflight Warnings

- SUMO load passed, but SUMO_HOME is not set so XML validation was disabled.
- Unused TLS state warnings were observed for 8487751470, COMPACT_V9_FIRE_STATION_ENTRY_TLS, cluster_11277565406_11277565407_11277565408_11277565409_#6more, cluster_11347624895_11414286294, and joinedS_11414286323_cluster_11414286309_7666741014.
- COMPACT_V9_FIRE_STATION_ENTRY_TLS has an unsafe green warning on lane -174870621#8_0; this did not block SUMO execution but should stay in the net cleanup backlog.

## Algorithm Review

- B04 is kept as no-control baseline only; its role is validating demand/signal plausibility.
- B4 controls the EV-route main-road Stage1 movement set, currently 17 controllable movements.
- Stage2 handles departure/merge hold; Stage3 uses route-order original Tau, TA proxy, phase restore, and expiry controls.
- Original Tau trigger uses effective Tau: `min(raw_tau * tau_scale, 1.0)`, while raw/effective/scale are logged in signal events.
- Candidate score is `10 * T_actual_EMV_sec + 1 * general_mean_travel_time_sec`; stuck/teleport/fail receive BO penalty.

## Top Candidates

- rank 1: init_020_tl21_ta70_ex32_ho14_du1 score=5937.89 EV=571.0 general=227.8921 tau=0.7 ext=32 hold=14 d_up=1
- rank 2: init_017_tl65_ta65_ex10_ho33_du2 score=6029.70 EV=580.0 general=229.703911 tau=0.65 ext=10 hold=33 d_up=2
- rank 3: init_013_tl64_ta70_ex19_ho18_du3 score=6450.84 EV=621.0 general=240.841509 tau=0.7 ext=19 hold=18 d_up=3
- rank 4: init_002_tl63_ta80_ex30_ho24_du1 score=6468.04 EV=623.0 general=238.040219 tau=0.8 ext=30 hold=24 d_up=1
- rank 5: init_011_tl12_ta70_ex40_ho18_du1 score=6859.80 EV=663.0 general=229.8 tau=0.7 ext=40 hold=18 d_up=1

## Remaining Bottlenecks

- The best ranked candidate improves EV travel time, but corridor bottlenecks remain in the focus S segments.
- B4_MOVEMENT_11 S15:upbound: speed=4.88 km/h, low_lt10=0.9027, waiting=8902.0 sec
- B4_MOVEMENT_04 S6:upbound: speed=8.264 km/h, low_lt10=0.7987, waiting=20131.0 sec
- B4_MOVEMENT_05 S7:upbound: speed=4.221 km/h, low_lt10=0.9498, waiting=32389.0 sec
- B4_MOVEMENT_06 S9:upbound: speed=1.603 km/h, low_lt10=0.994, waiting=77794.0 sec
- B4_MOVEMENT_07 S9:upbound: speed=1.518 km/h, low_lt10=1.0, waiting=63876.86 sec

## Tau Effective Fill

- Tau trigger uses effective fill, but S9 still often saturates at the scaled ceiling under u130 demand.
- S15:upbound: samples=68, raw_mean=0.9712, effective_p95=0.8256, hit_0p85=0
- S9:upbound: samples=117, raw_mean=0.9992, effective_p95=0.85, hit_0p85=108

## Outputs

- top20: `results/metrics/compact_v9_B4_theta_u130_init20/u130_init20_tau085_seed1_v2_20260605/top20_ranked.csv`
- segment summary: `09 Compact Corridor Baseline/tdata_signal/u130_init20_review/init20_candidate_segment_speed_summary.csv`
- Tau summary: `09 Compact Corridor Baseline/tdata_signal/u130_init20_review/init20_candidate_tau_event_summary.csv`
- archive manifest: `09 Compact Corridor Baseline/tdata_signal/u130_init20_review/target15_archive_manifest.json`
