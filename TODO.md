Testing

- Add test files for coordinator.py (44%), config_flow.py (20%), sensor.py (38%), binary_sensor.py (0%), switch.py (0%)
- Split test_calculation.py (2,197 lines, 142 tests) into per-class test files
- Add end-to-end integration tests wiring state providers → calculation → pipeline → diagnostics
- Add boundary tests for sun.py and cover constructors (zero/negative/NaN inputs)
