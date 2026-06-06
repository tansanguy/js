# B4 Theta OFAT Sensitivity

- generated_at: 2026-06-05T15:59:50.378814+00:00
- run_id: `u130_tlead_sensitive_patch_v2_20260606`
- net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- demand: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml`
- tau_scale: `0.85`
- tau_numerator_gamma: `5.0`

## Baseline

- theta: t_lead=21, tau=0.7, ext_max=32, hold_max=14, d_up=1
- result: status=PASS, EV=570.0 sec, general=238.904943 sec, score=5938.90

## Variable Sensitivity

- t_lead: high sensitivity, PASS 5/6, EV range=167.0 sec, max |delta EV|=259.0 sec
- tau: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- ext_max: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- hold_max: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- d_up: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec

## Best Single Changes

- baseline_tl21_ta70_ex32_ho14_du1: status=PASS, changed=baseline=, EV=570.0, general=238.904943, score=5938.90, delta_EV=0.0
- ofat_t_lead_10: status=PASS, changed=t_lead=10, EV=662.0, general=240.655856, score=6860.66, delta_EV=92.0
- ofat_t_lead_50: status=PASS, changed=t_lead=50, EV=682.0, general=226.691288, score=7046.69, delta_EV=112.0
- ofat_t_lead_35: status=PASS, changed=t_lead=35, EV=751.0, general=255.310287, score=7765.31, delta_EV=181.0
- ofat_t_lead_0: status=PASS, changed=t_lead=0, EV=821.0, general=261.985577, score=8471.99, delta_EV=251.0
- ofat_t_lead_95: status=PASS, changed=t_lead=95, EV=829.0, general=268.977236, score=8558.98, delta_EV=259.0
- ofat_t_lead_65: status=FAIL, changed=t_lead=65, EV=, general=170.440389, score=1000170.44, delta_EV=

## Outputs

- summary: `09 Compact Corridor Baseline/tdata_signal/u130_tlead_sensitive_patch_v2/ofat_sensitivity_summary.csv`
- variable summary: `09 Compact Corridor Baseline/tdata_signal/u130_tlead_sensitive_patch_v2/ofat_variable_sensitivity.csv`
- focus segment summary: `09 Compact Corridor Baseline/tdata_signal/u130_tlead_sensitive_patch_v2/ofat_focus_segment_speed_summary.csv`
