# B4 Theta OFAT Sensitivity

- generated_at: 2026-06-05T15:15:51.735960+00:00
- run_id: `u130_ofat_best_init020_tau085_20260606`
- net: `09 Compact Corridor Baseline/tdata_signal/nets/jungbu_compact_v9_B04_global_reality_mild.net.xml`
- demand: `data_prepared/compact_v9/demand/background_routes_compact_v9_B04_target15_u130.rou.xml`
- tau_scale: `0.85`

## Baseline

- theta: t_lead=21, tau=0.7, ext_max=32, hold_max=14, d_up=1
- result: status=PASS, EV=571.0 sec, general=227.8921 sec, score=5937.89

## Variable Sensitivity

- t_lead: medium sensitivity, PASS 6/6, EV range=64.0 sec, max |delta EV|=64.0 sec
- tau: high sensitivity, PASS 4/4, EV range=298.0 sec, max |delta EV|=298.0 sec
- ext_max: high sensitivity, PASS 4/5, EV range=324.0 sec, max |delta EV|=287.0 sec
- hold_max: medium sensitivity, PASS 5/5, EV range=49.0 sec, max |delta EV|=49.0 sec
- d_up: high sensitivity, PASS 0/2, EV range= sec, max |delta EV|= sec

## Best Single Changes

- ofat_ext_max_20: status=PASS, changed=ext_max=20, EV=534.0, general=213.717435, score=5553.72, delta_EV=-37.0
- baseline_tl21_ta70_ex32_ho14_du1: status=PASS, changed=baseline=, EV=571.0, general=227.8921, score=5937.89, delta_EV=0.0
- ofat_hold_max_24: status=PASS, changed=hold_max=24, EV=571.0, general=227.8921, score=5937.89, delta_EV=0.0
- ofat_hold_max_33: status=PASS, changed=hold_max=33, EV=571.0, general=227.8921, score=5937.89, delta_EV=0.0
- ofat_hold_max_40: status=PASS, changed=hold_max=40, EV=571.0, general=227.8921, score=5937.89, delta_EV=0.0
- ofat_hold_max_7: status=PASS, changed=hold_max=7, EV=571.0, general=227.8921, score=5937.89, delta_EV=0.0
- ofat_t_lead_10: status=PASS, changed=t_lead=10, EV=571.0, general=227.8921, score=5937.89, delta_EV=0.0
- ofat_t_lead_35: status=PASS, changed=t_lead=35, EV=571.0, general=227.8921, score=5937.89, delta_EV=0.0

## Outputs

- summary: `09 Compact Corridor Baseline/tdata_signal/u130_ofat_sensitivity/ofat_sensitivity_summary.csv`
- variable summary: `09 Compact Corridor Baseline/tdata_signal/u130_ofat_sensitivity/ofat_variable_sensitivity.csv`
- focus segment summary: `09 Compact Corridor Baseline/tdata_signal/u130_ofat_sensitivity/ofat_focus_segment_speed_summary.csv`
