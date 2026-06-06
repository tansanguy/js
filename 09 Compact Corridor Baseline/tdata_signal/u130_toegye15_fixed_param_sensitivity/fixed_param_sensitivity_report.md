# B4 Fixed Parameter Sensitivity

- generated_at: 2026-06-06T07:51:57.886286+00:00
- run_id: `codex_structure_full_inductive_20260606`
- net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- demand: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130_toegye15.rou.xml`
- fixed screened decision variables: alpha=1.15, t_lead=21.0, delta_T_thr=80.0, G_ext=32.0, Q_trig=0.0

## Baseline

- status=FAIL, EV= sec, general=138.12549 sec, score=1000138.13
- B04 baseline speed=14.304 km/h (target15_check=diagnostic_bypassed)
- stage3_preemption_count=7, original_tau_caseB=60, original_tau_hit_0p75=60

## Variable Sensitivity

- tau: high, PASS 0/6, EV_range= sec, tau_hit_0p75_range=
- tau_scale: high, PASS 0/4, EV_range= sec, tau_hit_0p75_range=
- tau_numerator_gamma: high, PASS 0/3, EV_range= sec, tau_hit_0p75_range=
- hold_max: high, PASS 1/3, EV_range= sec, tau_hit_0p75_range=
- d_up: high, PASS 0/2, EV_range= sec, tau_hit_0p75_range=

## Structure Preconfirmation Lock

- lock_status: `DIAGNOSTIC_LOCKED_BASELINE_FAIL`
- baseline_structure: `{"d_up": 1, "hold_max": 14.0, "tau": 0.75, "tau_numerator_gamma": 5.0, "tau_scale": 0.85}`
- candidate_structure: `{"d_up": 3, "hold_max": 33.0, "tau": 0.85, "tau_numerator_gamma": 5.0, "tau_scale": 0.8}`
- selected_structure: `{"d_up": 3, "hold_max": 33.0, "tau": 0.85, "tau_numerator_gamma": 5.0, "tau_scale": 0.8}`
- combined_confirmation: status=FAIL, reason=combined_lock_failed, EV=, score=1000119.26
- tau: provisional_diagnostic, selected=0.85, PASS 0/6, sensitivity=high, reason=baseline_failed_selected_by_runtime_original_tau_activation
- tau_scale: provisional_diagnostic, selected=0.8, PASS 0/4, sensitivity=high, reason=baseline_failed_selected_by_runtime_original_tau_activation
- tau_numerator_gamma: blocked_no_pass, selected=5.0, PASS 0/3, sensitivity=high, reason=no_passing_candidate_after_signal_burden_gate
- hold_max: provisional_pass_signal_tradeoff, selected=33.0, PASS 1/3, sensitivity=high, reason=baseline_failed_passing_candidate_exceeds_signal_burden_gate
- d_up: provisional_diagnostic, selected=3, PASS 0/2, sensitivity=high, reason=baseline_failed_selected_by_runtime_original_tau_activation

## Best Passing Candidates

- fixed_hold_max_33: status=PASS, changed=hold_max=33.0, EV=470.0, score=4902.61, caseB_segment=0, caseB_movement=0, original_tau_caseB=60, tau_hit_0p75=60
- fixed_combined_lock_tau0p85_scale1_gamma7_hold33_dup3: status=FAIL, changed=combined_lock=combined_lock, EV=, score=1000119.26, caseB_segment=0, caseB_movement=0, original_tau_caseB=177, tau_hit_0p75=184
- fixed_d_up_3: status=FAIL, changed=d_up=3, EV=, score=1000121.14, caseB_segment=0, caseB_movement=0, original_tau_caseB=179, tau_hit_0p75=179
- fixed_hold_max_7: status=FAIL, changed=hold_max=7.0, EV=, score=1000136.81, caseB_segment=0, caseB_movement=0, original_tau_caseB=60, tau_hit_0p75=60
- fixed_hold_max_24: status=FAIL, changed=hold_max=24.0, EV=, score=1000137.84, caseB_segment=0, caseB_movement=0, original_tau_caseB=58, tau_hit_0p75=58
- fixed_base_tau075_scale085_gamma5_hold14_dup1: status=FAIL, changed=baseline=, EV=, score=1000138.13, caseB_segment=0, caseB_movement=0, original_tau_caseB=60, tau_hit_0p75=60
- fixed_tau_0p6: status=FAIL, changed=tau=0.6, EV=, score=1000138.13, caseB_segment=0, caseB_movement=0, original_tau_caseB=60, tau_hit_0p75=60
- fixed_tau_0p65: status=FAIL, changed=tau=0.65, EV=, score=1000138.13, caseB_segment=0, caseB_movement=0, original_tau_caseB=60, tau_hit_0p75=60
- fixed_tau_0p7: status=FAIL, changed=tau=0.7, EV=, score=1000138.13, caseB_segment=0, caseB_movement=0, original_tau_caseB=60, tau_hit_0p75=60
- fixed_tau_0p8: status=FAIL, changed=tau=0.8, EV=, score=1000138.13, caseB_segment=0, caseB_movement=0, original_tau_caseB=60, tau_hit_0p75=60

## Outputs

- all values: `results/metrics/compact_v9_B4_fixed_param_sensitivity/codex_structure_full_inductive_20260606/fixed_param_all_values.csv`
- summary: `09 Compact Corridor Baseline/tdata_signal/u130_toegye15_fixed_param_sensitivity/fixed_param_sensitivity_summary.csv`
- variable summary: `09 Compact Corridor Baseline/tdata_signal/u130_toegye15_fixed_param_sensitivity/fixed_param_variable_sensitivity.csv`
- structure lock json: `09 Compact Corridor Baseline/tdata_signal/u130_toegye15_fixed_param_sensitivity/structure_param_lock_summary.json`
- structure lock csv: `09 Compact Corridor Baseline/tdata_signal/u130_toegye15_fixed_param_sensitivity/structure_param_lock.csv`
- structure preconfirm report: `09 Compact Corridor Baseline/tdata_signal/u130_toegye15_fixed_param_sensitivity/structure_param_preconfirm_report.md`
- next BO command: `python3 '09 Compact Corridor Baseline/run_b4_theta_bo.py' --structure-lock-json '09 Compact Corridor Baseline/tdata_signal/u130_toegye15_fixed_param_sensitivity/structure_param_lock_summary.json' --net-file '09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml' --background-route 'data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130_toegye15.rou.xml' --stage1-dir 'data_prepared/compact_v9/b4_stage1_u130_toegye15'`
