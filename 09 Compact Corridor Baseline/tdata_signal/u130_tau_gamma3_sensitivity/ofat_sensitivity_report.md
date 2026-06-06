# B4 Theta OFAT Sensitivity

- generated_at: 2026-06-05T15:31:48.250644+00:00
- run_id: `u130_tau_gamma3_sensitivity_20260606`
- net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- demand: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml`
- tau_scale: `0.85`
- tau_numerator_gamma: `3.0`

## Baseline

- theta: t_lead=21, tau=0.7, ext_max=32, hold_max=14, d_up=1
- result: status=PASS, EV=571.0 sec, general=227.8921 sec, score=5937.89

## Variable Sensitivity

- t_lead: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- tau: high sensitivity, PASS 4/4, EV range=298.0 sec, max |delta EV|=298.0 sec
- ext_max: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- hold_max: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- d_up: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec

## Best Single Changes

- baseline_tl21_ta70_ex32_ho14_du1: status=PASS, changed=baseline=, EV=571.0, general=227.8921, score=5937.89, delta_EV=0.0
- ofat_tau_0p65: status=PASS, changed=tau=0.65, EV=571.0, general=227.8921, score=5937.89, delta_EV=0.0
- ofat_tau_0p75: status=PASS, changed=tau=0.75, EV=571.0, general=227.8921, score=5937.89, delta_EV=0.0
- ofat_tau_0p8: status=PASS, changed=tau=0.8, EV=754.0, general=250.618538, score=7790.62, delta_EV=183.0
- ofat_tau_0p85: status=PASS, changed=tau=0.85, EV=869.0, general=279.060185, score=8969.06, delta_EV=298.0

## Outputs

- summary: `09 Compact Corridor Baseline/tdata_signal/u130_tau_gamma3_sensitivity/ofat_sensitivity_summary.csv`
- variable summary: `09 Compact Corridor Baseline/tdata_signal/u130_tau_gamma3_sensitivity/ofat_variable_sensitivity.csv`
- focus segment summary: `09 Compact Corridor Baseline/tdata_signal/u130_tau_gamma3_sensitivity/ofat_focus_segment_speed_summary.csv`
