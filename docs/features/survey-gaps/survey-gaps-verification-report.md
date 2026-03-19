# Survey Gaps — Verification Report

## Test Results

| Phase | Command | Environment | Result | Notes |
|-------|---------|-------------|--------|-------|
| Unit tests | `pytest tests/unit/ -q` | Python 3.9.6, macOS | 1010 passed, 8 failed (pre-existing), 4 warnings | 66s runtime |
| Integration tests | `pytest tests/integration/test_survey_gaps_integration.py -q` | Python 3.9.6, macOS | 63 passed, 2 skipped | 2s runtime |
| Embedding tests | `pytest tests/unit/test_embedding_retrieval.py -q` | Python 3.9.6, macOS | 13 passed, 2 skipped (fastembed) | 0.5s |
| Infeasibility tests | `pytest tests/unit/test_infeasibility.py -q` | Python 3.9.6, macOS | 12 passed | 0.3s |
| Confirmation tests | `pytest tests/unit/test_confirmation.py -q` | Python 3.9.6, macOS | 35 passed | 0.5s |
| SoM tests | `pytest tests/unit/test_som.py -q` | Python 3.9.6, macOS | 13 passed | 0.3s |
| Dual-res tests | `pytest tests/unit/test_dual_resolution.py -q` | Python 3.9.6, macOS | 7 passed | 0.2s |
| World-state tests | `pytest tests/unit/test_world_state.py -q` | Python 3.9.6, macOS | 17 passed | 0.3s |
| Lookahead tests | `pytest tests/unit/test_lookahead.py -q` | Python 3.9.6, macOS | 15 passed | 0.3s |

## Skipped Tests
- 2 embedding tests skipped: require `fastembed` optional dependency (`pip install -e '.[embeddings]'`)

## Pre-existing Failures (not from survey-gaps)
- 7 in `test_vision_arch_improvements.py` — gemini-2.5-flash missing from coordinate space map
- 1 in `test_scroll_action.py` — scroll prompt action list mismatch

## Manual Findings
- Manual test script at `docs/features/survey-gaps/ (no dedicated manual test script; see survey-gaps-customer-scenarios.md)` (not executed — requires live macOS desktop + vision server)

## Blocked Checks
- E2E testing requires live macOS desktop with accessibility permissions and vision server running
