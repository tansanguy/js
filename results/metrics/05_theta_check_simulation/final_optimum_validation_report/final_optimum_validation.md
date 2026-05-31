# Final Optimum Validation

## Run Metadata

| screening_run_id | final_run_id | final_status | task_count | completed_task_count | seed | depart_min | depart_max | b2_params | mode_repeats | selected_routes | output_path |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20260531T150223_686366Z0000 | 20260531T151900_688506Z0000 | PASS | 183 | 183 | 20260531 | 300.0 | 2400.0 | 05_theta_check_simulation/final_optimum_b2_parameter_sets.csv | {'B00': 1, 'B0': 30, 'B2': 30} | ER_ACC_010 ER_ACC_008 ER_ACC_012 | results/metrics/05_theta_check_simulation/final_optimum_validation/20260531T151900_688506Z0000 |

## Commands

Screening:

```bash
python 05_theta_check_simulation/parameter_sim.py --modes B00 B0 B2 --routes-csv 05_theta_check_simulation/routes/b0_valid_18_routes.csv --b2-params 05_theta_check_simulation/final_optimum_b2_parameter_sets.csv --depart-min 300.0 --depart-max 2400.0 --seed 20260531 --repeats 1 --workers 6 --output-prefix final_optimum_route_screening --resume
```

Final validation resume command:

```bash
python 05_theta_check_simulation/parameter_sim.py --routes ER_ACC_010 ER_ACC_008 ER_ACC_012 --modes B00 B0 B2 --routes-csv 05_theta_check_simulation/routes/b0_valid_18_routes.csv --b2-params 05_theta_check_simulation/final_optimum_b2_parameter_sets.csv --depart-min 300.0 --depart-max 2400.0 --seed 20260531 --repeats 30 --b00-repeats 1 --b0-repeats 30 --b2-repeats 30 --workers 3 --output-prefix final_optimum_validation --resume
```

## Selected Routes

| rank | route_id | B2_vs_B0_delta_sec | intervention_count | final_status |
| --- | --- | --- | --- | --- |
| 1 | ER_ACC_010 | -117.00 | 10 | WARNING |
| 2 | ER_ACC_008 | -102.00 | 4 | WARNING |
| 3 | ER_ACC_012 | -81.00 | 7 | WARNING |

## Final Comparison

| scope | route_id | pair_count | B0_mean_sec | B2_mean_sec | delta_mean_sec | improvement_pct_mean | delta_ci95_low_sec | delta_ci95_high_sec | warning_count | fail_count |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| route | ER_ACC_008 | 30 | 232.77 | 132.47 | -100.30 | 42.79 | -108.63 | -91.97 | 0 | 0 |
| route | ER_ACC_010 | 30 | 257.80 | 148.33 | -109.47 | 42.72 | -114.34 | -104.59 | 0 | 0 |
| route | ER_ACC_012 | 30 | 225.93 | 144.50 | -81.43 | 35.83 | -89.14 | -73.73 | 0 | 0 |
| overall | ALL | 90 | 238.83 | 141.77 | -97.07 | 40.44 | -101.80 | -92.33 | 0 | 0 |

## Pair Counts

| route_id | pair_count |
| --- | --- |
| ER_ACC_008 | 30 |
| ER_ACC_010 | 30 |
| ER_ACC_012 | 30 |

## Departure Schedule

Full paired departure schedule: `results/metrics/05_theta_check_simulation/final_optimum_validation_report/final_departure_schedule.csv`
