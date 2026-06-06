# B4 Theta OFAT Sensitivity

- generated_at: 2026-06-05T16:03:15.332447+00:00
- run_id: `u130_holdmax_sensitive_patch_v2_20260606`
- net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- demand: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml`
- tau_scale: `0.85`
- tau_numerator_gamma: `5.0`

## Baseline

- theta: t_lead=21, tau=0.7, ext_max=32, hold_max=14, d_up=1
- result: status=PASS, EV=570.0 sec, general=238.904943 sec, score=5938.90

## Variable Sensitivity

- t_lead: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- tau: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- ext_max: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- hold_max: high sensitivity, PASS 5/5, EV range=205.0 sec, max |delta EV|=259.0 sec
- d_up: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec

## Best Single Changes

- baseline_tl21_ta70_ex32_ho14_du1: status=PASS, changed=baseline=, EV=570.0, general=238.904943, score=5938.90, delta_EV=0.0
- ofat_hold_max_33: status=PASS, changed=hold_max=33, EV=624.0, general=231.875686, score=6471.88, delta_EV=54.0
- ofat_hold_max_40: status=PASS, changed=hold_max=40, EV=651.0, general=231.17603, score=6741.18, delta_EV=81.0
- ofat_hold_max_24: status=PASS, changed=hold_max=24, EV=721.0, general=241.436644, score=7451.44, delta_EV=151.0
- ofat_hold_max_7: status=PASS, changed=hold_max=7, EV=769.0, general=236.492481, score=7926.49, delta_EV=199.0
- ofat_hold_max_1: status=PASS, changed=hold_max=1, EV=829.0, general=276.153226, score=8566.15, delta_EV=259.0

## Outputs

- summary: `09 Compact Corridor Baseline/tdata_signal/u130_holdmax_sensitive_patch_v2/ofat_sensitivity_summary.csv`
- variable summary: `09 Compact Corridor Baseline/tdata_signal/u130_holdmax_sensitive_patch_v2/ofat_variable_sensitivity.csv`
- focus segment summary: `09 Compact Corridor Baseline/tdata_signal/u130_holdmax_sensitive_patch_v2/ofat_focus_segment_speed_summary.csv`
