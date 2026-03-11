# Changelog

## 2026-03-10

### Added
- Added a persistent macOS status UI with a floating overlay and menu bar item that show the current goal, current step, and live status messages.
- Added shared status event helpers and automated coverage for the status UI and skill fallback path.
- Added targeted regression tests for coordinate scaling, retry behavior, verifier improvements, status UI behavior, and Amazon skill fallback execution.

### Changed
- Corrected screenshot-space to macOS screen-space coordinate mapping so grounded clicks land on the intended UI element on scaled displays.
- Reworked retry handling so failed steps execute genuinely different recovery actions instead of replaying ignored metadata.
- Strengthened accessibility-first grounding and action verification while keeping screenshot OCR disabled.
- Updated the Amazon return skill to use browser-agnostic navigation, conditional login waiting, top-of-page recovery, and more specific search-box targeting.
- Made compiled and replanned key presses tolerant of both `keys=[...]` and legacy `key=\"...\"` parameter shapes.

### Fixed
- Fixed grounding result normalization and confidence propagation across the router and orchestrator.
- Fixed replanning to preserve desktop context.
- Fixed status overlay persistence across Spaces and browser transitions, and extended post-run linger time so the final status remains visible after the agent exits.
- Fixed benchmark/backend and accessibility test harness issues that were causing suite instability.
- Fixed skill keyword fallback so skills with missing required params are not selected as invalid no-op matches.

### Tested
- `pytest -q tests/unit/test_orchestrator_new.py tests/integration/test_status_and_skill_fallback_integration.py tests/test_status_overlay.py`
- `pytest -q -m 'not e2e'` -> `1152 passed, 4 skipped, 5 deselected`
- `pytest -q -m e2e` -> `5 passed, 3 skipped, 1153 deselected`
- Manual live validation of screenshot+model flow, orchestrator smoke tests, overlay persistence in-browser, and Amazon order-search flow behavior.
