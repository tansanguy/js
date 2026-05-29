# Step 13 Single-Terminal Signal Experiment

## Purpose

This diagnostic experiment enables one corridor priority terminal at a time. It decomposes B1 behavior so each TLS contribution can be reviewed independently before selected-terminal B1 or B2 optimization.

## Method

- Source terminals: `data_prepared/signals/priority_terminal_candidates.csv`
- Terminal filter: `install_candidate_status=PASS`
- Background demand: `data_prepared/demand/background_routes_am_imputed_a17_a19_scale_0p15.rou.xml`
- Excluded route: `ER_ACC_013` because B0 has emergency teleport.
- Safety: no direct phase-state rewrite, no yellow/clearance skip, existing phase sequence only.

## ER_ACC_002 Sweep

- run status: `COMPLETE`
- terminal count: `20`
- status counts: `{'PASS': 2, 'WARNING': 18, 'FAIL': 0}`
- best terminal: `{'terminal_id': 'PT_001', 'tls_id': '11203059756', 'run_count': 1, 'route_coverage_count': 1, 'route_coverage_ratio': 1.0, 'covered_route_ids': ['ER_ACC_002'], 'controlled_route_count': 0, 'controlled_route_ids': [], 'pass_count': 0, 'warning_count': 1, 'fail_count': 0, 'avg_improvement_pct': 0.0, 'avg_delta_sec': 0.0, 'controlled_tls_count': 0, 'skipped_tls_count': 8, 'disabled_tls_count': 8, 'intervention_count': 0, 'green_extension_count': 0, 'phase_switch_count': 0}`
- worst terminal: `{'terminal_id': 'PT_019', 'tls_id': 'joinedS_4273893706_4273893707_7335400058_cluster_13348213189_13348213190_13348213191', 'run_count': 1, 'route_coverage_count': 1, 'route_coverage_ratio': 1.0, 'covered_route_ids': ['ER_ACC_002'], 'controlled_route_count': 1, 'controlled_route_ids': ['ER_ACC_002'], 'pass_count': 1, 'warning_count': 0, 'fail_count': 0, 'avg_improvement_pct': -1.694915, 'avg_delta_sec': 3.0, 'controlled_tls_count': 1, 'skipped_tls_count': 7, 'disabled_tls_count': 7, 'intervention_count': 1, 'green_extension_count': 1, 'phase_switch_count': 0}`

### ER_ACC_002 Terminal Counts

| terminal | tls_id | PASS | WARNING | FAIL | coverage | controlled_routes | controlled | skipped | disabled | interventions | avg_delta_sec | avg_improvement_pct |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PT_001 | 11203059756 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 8 | 0 | 0.0 | 0.0 |
| PT_002 | 11346754524 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 8 | 0 | 0.0 | 0.0 |
| PT_003 | 11346754525 | 0 | 1 | 0 | 1 | 1 | 1 | 7 | 7 | 1 | 1.0 | -0.564972 |
| PT_004 | 13732389937 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 8 | 0 | 0.0 | 0.0 |
| PT_005 | 13732389938 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 8 | 0 | 0.0 | 0.0 |
| PT_006 | 4202879197 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 8 | 0 | 0.0 | 0.0 |
| PT_007 | 436870729 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 8 | 0 | 0.0 | 0.0 |
| PT_008 | 7311080426 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 8 | 0 | 0.0 | 0.0 |
| PT_009 | 7335400049 | 0 | 1 | 0 | 1 | 1 | 1 | 7 | 7 | 1 | 0.0 | 0.0 |
| PT_010 | cluster_11203041632_11203041633_11203041634_11203041635_#5more | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 7 | 0 | 0.0 | 0.0 |
| PT_011 | cluster_11277565408_11277565409_414685823_5851251280 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 8 | 0 | 0.0 | 0.0 |
| PT_012 | cluster_12785870414_3337522189 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 8 | 0 | 0.0 | 0.0 |
| PT_013 | cluster_1326096615_3849700763 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 7 | 0 | 0.0 | 0.0 |
| PT_014 | cluster_13348293702_13348293703_414685829 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 8 | 0 | 0.0 | 0.0 |
| PT_015 | cluster_3846534041_436877431 | 1 | 0 | 0 | 1 | 1 | 1 | 7 | 7 | 1 | 0.0 | 0.0 |
| PT_016 | joinedS_11139302899_11139302904_11178710208_cluster_11139302901_5593950669_5593950674 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 8 | 0 | 0.0 | 0.0 |
| PT_017 | joinedS_11203001806_11203001814_13721232731_5851251250_#6more | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 7 | 0 | 0.0 | 0.0 |
| PT_018 | joinedS_11203052957_cluster_11203052955_11203052956_11203052960_11203052961_#11more | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 7 | 0 | 0.0 | 0.0 |
| PT_019 | joinedS_4273893706_4273893707_7335400058_cluster_13348213189_13348213190_13348213191 | 1 | 0 | 0 | 1 | 1 | 1 | 7 | 7 | 1 | 3.0 | -1.694915 |
| PT_020 | joinedS_4276255150_4276255151_4276255152_cluster_4276255149_436839431 | 0 | 1 | 0 | 1 | 0 | 0 | 8 | 8 | 0 | 0.0 | 0.0 |

## 18-Route Sweep

run_status `COMPLETE`, rows `360`, status `{'PASS': 182, 'WARNING': 178, 'FAIL': 0}`, best `{'terminal_id': 'PT_007', 'tls_id': '436870729', 'run_count': 18, 'route_coverage_count': 18, 'route_coverage_ratio': 1.0, 'covered_route_ids': ['ER_ACC_001', 'ER_ACC_002', 'ER_ACC_003', 'ER_ACC_004', 'ER_ACC_005', 'ER_ACC_006', 'ER_ACC_007', 'ER_ACC_008', 'ER_ACC_009', 'ER_ACC_010', 'ER_ACC_011', 'ER_ACC_012', 'ER_ACC_014', 'ER_ACC_015', 'ER_ACC_016', 'ER_ACC_017', 'ER_ACC_018', 'ER_ACC_019'], 'controlled_route_count': 1, 'controlled_route_ids': ['ER_ACC_008'], 'pass_count': 9, 'warning_count': 9, 'fail_count': 0, 'avg_improvement_pct': 0.115741, 'avg_delta_sec': -0.111111, 'controlled_tls_count': 1, 'skipped_tls_count': 102, 'disabled_tls_count': 102, 'intervention_count': 1, 'green_extension_count': 1, 'phase_switch_count': 0}`

### 18-Route Terminal Counts

| terminal | tls_id | PASS | WARNING | FAIL | coverage | controlled_routes | controlled | skipped | disabled | interventions | avg_delta_sec | avg_improvement_pct |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PT_001 | 11203059756 | 9 | 9 | 0 | 18 | 1 | 1 | 102 | 102 | 1 | 0.0 | 0.0 |
| PT_002 | 11346754524 | 9 | 9 | 0 | 18 | 2 | 2 | 101 | 101 | 2 | 0.0 | 0.0 |
| PT_003 | 11346754525 | 6 | 12 | 0 | 18 | 10 | 10 | 93 | 93 | 10 | 0.055556 | -0.025085 |
| PT_004 | 13732389937 | 8 | 10 | 0 | 18 | 2 | 2 | 101 | 101 | 2 | 0.055556 | -0.029395 |
| PT_005 | 13732389938 | 8 | 10 | 0 | 18 | 2 | 2 | 101 | 101 | 2 | 0.0 | 0.0 |
| PT_006 | 4202879197 | 9 | 9 | 0 | 18 | 0 | 0 | 103 | 102 | 0 | 0.0 | 0.0 |
| PT_007 | 436870729 | 9 | 9 | 0 | 18 | 1 | 1 | 102 | 102 | 1 | -0.111111 | 0.115741 |
| PT_008 | 7311080426 | 9 | 9 | 0 | 18 | 1 | 1 | 102 | 102 | 1 | 0.0 | 0.0 |
| PT_009 | 7335400049 | 6 | 12 | 0 | 18 | 13 | 13 | 90 | 90 | 13 | 0.055556 | -0.027367 |
| PT_010 | cluster_11203041632_11203041633_11203041634_11203041635_#5more | 9 | 9 | 0 | 18 | 0 | 0 | 103 | 100 | 0 | 0.0 | 0.0 |
| PT_011 | cluster_11277565408_11277565409_414685823_5851251280 | 9 | 9 | 0 | 18 | 0 | 0 | 103 | 103 | 0 | 0.0 | 0.0 |
| PT_012 | cluster_12785870414_3337522189 | 9 | 9 | 0 | 18 | 0 | 0 | 103 | 102 | 0 | 0.0 | 0.0 |
| PT_013 | cluster_1326096615_3849700763 | 9 | 9 | 0 | 18 | 0 | 0 | 103 | 85 | 0 | 0.0 | 0.0 |
| PT_014 | cluster_13348293702_13348293703_414685829 | 8 | 10 | 0 | 18 | 3 | 3 | 100 | 100 | 3 | -0.055556 | 0.050505 |
| PT_015 | cluster_3846534041_436877431 | 11 | 7 | 0 | 18 | 18 | 18 | 85 | 85 | 18 | 0.111111 | -0.057567 |
| PT_016 | joinedS_11139302899_11139302904_11178710208_cluster_11139302901_5593950669_5593950674 | 10 | 8 | 0 | 18 | 1 | 1 | 102 | 102 | 1 | 2.333333 | -1.149425 |
| PT_017 | joinedS_11203001806_11203001814_13721232731_5851251250_#6more | 9 | 9 | 0 | 18 | 0 | 0 | 103 | 102 | 0 | 0.0 | 0.0 |
| PT_018 | joinedS_11203052957_cluster_11203052955_11203052956_11203052960_11203052961_#11more | 8 | 10 | 0 | 18 | 2 | 2 | 101 | 96 | 2 | 0.444444 | -0.267344 |
| PT_019 | joinedS_4273893706_4273893707_7335400058_cluster_13348213189_13348213190_13348213191 | 17 | 1 | 0 | 18 | 16 | 16 | 87 | 87 | 16 | 0.388889 | -0.246493 |
| PT_020 | joinedS_4276255150_4276255151_4276255152_cluster_4276255149_436839431 | 10 | 8 | 0 | 18 | 2 | 2 | 101 | 101 | 2 | 2.055556 | -2.023426 |

## Next Step

Review best/worst terminals and build a selected-terminal B1 candidate using only terminals with stable positive effect. This is still not B2 or Bayesian Optimization.
