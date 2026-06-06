# B4 Theta OFAT Sensitivity

- generated_at: 2026-06-05T15:47:44.121675+00:00
- run_id: `u130_tlead_sensitive_patch_20260606`
- net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- demand: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml`
- tau_scale: `0.85`
- tau_numerator_gamma: `5.0`

## Baseline

- theta: t_lead=21, tau=0.7, ext_max=32, hold_max=14, d_up=1
- result: status=FAIL, EV= sec, general=172.304348 sec, score=1000172.30

## Variable Sensitivity

- t_lead: high sensitivity, PASS 0/6, EV range= sec, max |delta EV|= sec
- tau: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- ext_max: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- hold_max: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec
- d_up: low sensitivity, PASS 0/0, EV range= sec, max |delta EV|= sec

## Best Single Changes

- ofat_t_lead_65: status=FAIL, changed=t_lead=65, EV=, general=131.787879, score=1000131.79, delta_EV=
- ofat_t_lead_95: status=FAIL, changed=t_lead=95, EV=, general=131.817391, score=1000131.82, delta_EV=
- ofat_t_lead_10: status=FAIL, changed=t_lead=10, EV=, general=149.006472, score=1000149.01, delta_EV=
- ofat_t_lead_0: status=FAIL, changed=t_lead=0, EV=, general=152.490506, score=1000152.49, delta_EV=
- ofat_t_lead_35: status=FAIL, changed=t_lead=35, EV=, general=156.310241, score=1000156.31, delta_EV=
- baseline_tl21_ta70_ex32_ho14_du1: status=FAIL, changed=baseline=, EV=, general=172.304348, score=1000172.30, delta_EV=
- ofat_t_lead_50: status=FAIL, changed=t_lead=50, EV=, general=182.92665, score=1000182.93, delta_EV=

## Outputs

- summary: `09 Compact Corridor Baseline/tdata_signal/u130_tlead_sensitive_patch/ofat_sensitivity_summary.csv`
- variable summary: `09 Compact Corridor Baseline/tdata_signal/u130_tlead_sensitive_patch/ofat_variable_sensitivity.csv`
- focus segment summary: `09 Compact Corridor Baseline/tdata_signal/u130_tlead_sensitive_patch/ofat_focus_segment_speed_summary.csv`
