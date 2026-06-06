# B4 Theta OFAT Sensitivity

- generated_at: 2026-06-05T15:50:53.819791+00:00
- run_id: `u130_holdmax_sensitive_patch_20260606`
- net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- demand: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml`
- tau_scale: `0.85`
- tau_numerator_gamma: `5.0`

## Baseline

- theta: t_lead=21, tau=0.7, ext_max=32, hold_max=14, d_up=1
- result: status=FAIL, EV= sec, general=172.304348 sec, score=1000172.30

## Variable Sensitivity

- t_lead: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- tau: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- ext_max: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- hold_max: high sensitivity, PASS 4/5, EV range=564.0 sec, max |delta EV|=922.0 sec
- d_up: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec

## Best Single Changes

- ofat_hold_max_33: status=PASS, changed=hold_max=33, EV=358.0, general=180.935802, score=3760.94, delta_EV=358.0
- ofat_hold_max_7: status=PASS, changed=hold_max=7, EV=654.0, general=218.904959, score=6758.90, delta_EV=654.0
- ofat_hold_max_24: status=PASS, changed=hold_max=24, EV=745.0, general=250.7, score=7700.70, delta_EV=745.0
- ofat_hold_max_40: status=PASS, changed=hold_max=40, EV=922.0, general=272.083841, score=9492.08, delta_EV=922.0
- ofat_hold_max_1: status=FAIL, changed=hold_max=1, EV=, general=161.068536, score=1000161.07, delta_EV=
- baseline_tl21_ta70_ex32_ho14_du1: status=FAIL, changed=baseline=, EV=, general=172.304348, score=1000172.30, delta_EV=

## Outputs

- summary: `09 Compact Corridor Baseline/tdata_signal/u130_holdmax_sensitive_patch/ofat_sensitivity_summary.csv`
- variable summary: `09 Compact Corridor Baseline/tdata_signal/u130_holdmax_sensitive_patch/ofat_variable_sensitivity.csv`
- focus segment summary: `09 Compact Corridor Baseline/tdata_signal/u130_holdmax_sensitive_patch/ofat_focus_segment_speed_summary.csv`
