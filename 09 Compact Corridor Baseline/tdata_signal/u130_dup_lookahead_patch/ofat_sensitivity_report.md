# B4 Theta OFAT Sensitivity

- generated_at: 2026-06-05T16:18:53.670697+00:00
- run_id: `u130_dup_lookahead_patch_20260606`
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
- hold_max: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- d_up: medium sensitivity, PASS 2/2, EV range=2.0 sec, max |delta EV|=59.0 sec

## Best Single Changes

- baseline_tl21_ta70_ex32_ho14_du1: status=PASS, changed=baseline=, EV=570.0, general=238.904943, score=5938.90, delta_EV=0.0
- ofat_d_up_3: status=PASS, changed=d_up=3, EV=627.0, general=223.217069, score=6493.22, delta_EV=57.0
- ofat_d_up_2: status=PASS, changed=d_up=2, EV=629.0, general=232.363636, score=6522.36, delta_EV=59.0

## Outputs

- summary: `09 Compact Corridor Baseline/tdata_signal/u130_dup_lookahead_patch/ofat_sensitivity_summary.csv`
- variable summary: `09 Compact Corridor Baseline/tdata_signal/u130_dup_lookahead_patch/ofat_variable_sensitivity.csv`
- focus segment summary: `09 Compact Corridor Baseline/tdata_signal/u130_dup_lookahead_patch/ofat_focus_segment_speed_summary.csv`
