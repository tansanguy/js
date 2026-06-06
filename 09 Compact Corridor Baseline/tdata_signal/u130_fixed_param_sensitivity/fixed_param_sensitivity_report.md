# B4 Fixed Parameter Sensitivity

- generated_at: 2026-06-06T03:10:49.388689+00:00
- run_id: `fixed_param_all_currentstress_20260606`
- net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- demand: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml`
- fixed screened decision variables: alpha=1.15, t_lead=21.0, delta_T_thr=80.0, G_ext=32.0, Q_trig=0.0

## Baseline

- status=PASS, EV=922.0 sec, general=268.672697 sec, score=9488.67
- B04 baseline speed=6.593 km/h (target15_check=diagnostic_bypassed)
- stage3_preemption_count=7, original_tau_hit_0p75=119

## Variable Sensitivity

- tau: high, PASS 2/6, EV_range=1.0 sec, tau_hit_0p75_range=19.0
- tau_scale: high, PASS 1/4, EV_range= sec, tau_hit_0p75_range=
- tau_numerator_gamma: high, PASS 2/3, EV_range=194.0 sec, tau_hit_0p75_range=50.0
- hold_max: high, PASS 3/3, EV_range=405.0 sec, tau_hit_0p75_range=8.0
- d_up: high, PASS 1/2, EV_range= sec, tau_hit_0p75_range=

## Best Passing Candidates

- fixed_hold_max_24: status=PASS, changed=hold_max=24.0, EV=462.0, score=4817.91, caseB_segment=0, caseB_movement=0, tau_hit_0p75=113
- fixed_tau_numerator_gamma_1: status=PASS, changed=tau_numerator_gamma=1.0, EV=728.0, score=7528.89, caseB_segment=0, caseB_movement=0, tau_hit_0p75=169
- fixed_hold_max_7: status=PASS, changed=hold_max=7.0, EV=758.0, score=7807.13, caseB_segment=0, caseB_movement=0, tau_hit_0p75=121
- fixed_hold_max_33: status=PASS, changed=hold_max=33.0, EV=867.0, score=8914.38, caseB_segment=0, caseB_movement=0, tau_hit_0p75=113
- fixed_tau_0p85: status=PASS, changed=tau=0.85, EV=921.0, score=9454.28, caseB_segment=0, caseB_movement=0, tau_hit_0p75=100
- fixed_base_tau075_scale085_gamma5_hold14_dup1: status=PASS, changed=baseline=, EV=922.0, score=9488.67, caseB_segment=0, caseB_movement=0, tau_hit_0p75=119
- fixed_tau_0p8: status=PASS, changed=tau=0.8, EV=922.0, score=9488.67, caseB_segment=0, caseB_movement=0, tau_hit_0p75=119
- fixed_tau_numerator_gamma_7: status=PASS, changed=tau_numerator_gamma=7.0, EV=922.0, score=9488.67, caseB_segment=0, caseB_movement=0, tau_hit_0p75=119
- fixed_tau_scale_0p8: status=PASS, changed=tau_scale=0.8, EV=922.0, score=9488.67, caseB_segment=0, caseB_movement=0, tau_hit_0p75=119
- fixed_d_up_2: status=PASS, changed=d_up=2, EV=1323.0, score=13534.20, caseB_segment=0, caseB_movement=0, tau_hit_0p75=211

## Outputs

- all values: `results/metrics/compact_v9_B4_fixed_param_sensitivity/fixed_param_all_currentstress_20260606/fixed_param_all_values.csv`
- summary: `09 Compact Corridor Baseline/tdata_signal/u130_fixed_param_sensitivity/fixed_param_sensitivity_summary.csv`
- variable summary: `09 Compact Corridor Baseline/tdata_signal/u130_fixed_param_sensitivity/fixed_param_variable_sensitivity.csv`
