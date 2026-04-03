HACS

- ✅ PR #6130 - HACS Default Integration Submission
  - Status: OPEN, in review queue
  - Created: 2026-03-10 16:14:36Z
  - Branch: hacs/default:master ← add-adaptive-cover-pro
  - Changes: Added jrhubott/adaptive-cover-pro to integrations file
  - Label: New default repository
  - hacs-bot comment posted: "submission in review queue"
  - Processing: oldest-first queue, wait for HACS team review
  - Current version: v2.12.0
  - Related: Failed attempts #6128, #6129 (both instant rejected)
  - Action: WAIT - do not comment or create new PRs

Testing

- Add test files for coordinator.py (44%), config_flow.py (20%), sensor.py (38%), binary_sensor.py (0%), switch.py (0%)
- ~~Split test_calculation.py (2,197 lines, 142 tests) into per-class test files~~
- ~~Add end-to-end integration tests wiring state providers → calculation → pipeline → diagnostics~~
- Add boundary tests for sun.py and cover constructors (zero/negative/NaN inputs)
