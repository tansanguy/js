# B4 Fixed Parameter Sensitivity

- generated_at: 2026-06-06T06:52:40.644012+00:00
- run_id: `active15_tau_sensitivity_after_stage3_fix_20260606`
- net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- demand: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_active15.rou.xml`
- fixed screened decision variables: alpha=1.15, t_lead=21.0, delta_T_thr=80.0, G_ext=32.0, Q_trig=0.0

## Baseline

- status=PASS, EV=619.0 sec, general=269.636054 sec, score=6459.64
- B04 baseline speed=15.069 km/h (target15_check=required)
- stage3_preemption_count=18, original_tau_hit_0p75=0

## Variable Sensitivity

- tau: low, PASS 6/6, EV_range=0.0 sec, tau_hit_0p75_range=0.0
- tau_scale: low, PASS 0/0, EV_range= sec, tau_hit_0p75_range=
- tau_numerator_gamma: low, PASS 0/0, EV_range= sec, tau_hit_0p75_range=
- hold_max: low, PASS 0/0, EV_range= sec, tau_hit_0p75_range=
- d_up: low, PASS 0/0, EV_range= sec, tau_hit_0p75_range=

## Best Passing Candidates

- fixed_base_tau075_scale085_gamma5_hold14_dup1: status=PASS, changed=baseline=, EV=619.0, score=6459.64, caseB_segment=0, caseB_movement=0, tau_hit_0p75=0
- fixed_tau_0p6: status=PASS, changed=tau=0.6, EV=619.0, score=6459.64, caseB_segment=0, caseB_movement=0, tau_hit_0p75=0
- fixed_tau_0p65: status=PASS, changed=tau=0.65, EV=619.0, score=6459.64, caseB_segment=0, caseB_movement=0, tau_hit_0p75=0
- fixed_tau_0p7: status=PASS, changed=tau=0.7, EV=619.0, score=6459.64, caseB_segment=0, caseB_movement=0, tau_hit_0p75=0
- fixed_tau_0p8: status=PASS, changed=tau=0.8, EV=619.0, score=6459.64, caseB_segment=0, caseB_movement=0, tau_hit_0p75=0
- fixed_tau_0p85: status=PASS, changed=tau=0.85, EV=619.0, score=6459.64, caseB_segment=0, caseB_movement=0, tau_hit_0p75=0
- fixed_tau_0p9: status=PASS, changed=tau=0.9, EV=619.0, score=6459.64, caseB_segment=0, caseB_movement=0, tau_hit_0p75=0

## Outputs

- all values: `results/metrics/compact_v9_B4_fixed_param_sensitivity/active15_tau_sensitivity_after_stage3_fix_20260606/fixed_param_all_values.csv`
- summary: `09 Compact Corridor Baseline/tdata_signal/active15_fixed_param_sensitivity/fixed_param_sensitivity_summary.csv`
- variable summary: `09 Compact Corridor Baseline/tdata_signal/active15_fixed_param_sensitivity/fixed_param_variable_sensitivity.csv`
