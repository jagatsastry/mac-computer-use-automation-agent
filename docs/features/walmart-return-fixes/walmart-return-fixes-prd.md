# PRD: Walmart Return Fixes

**Date**: 2026-03-13
**Status**: Draft
**Input docs**: [Customer Report](walmart-return-customer-report.md) | [SOTA Research](walmart-return-fixes-sota.md) | [Codebase Analysis](walmart-return-fixes-codebase.md)

---

## Problem Statement

Walmart return automation fails all 3 customer test scenarios (0/3 pass) due to 6 bugs spanning plan parsing, navigation, wait handling, scroll recovery, skill registry hygiene, and observability. Two bugs are P0 (block all scenarios), two are P1 (degrade execution), and two are P2 (hinder debugging and introduce latent risk).

The failures are not vision-model or LLM-quality issues — the LLM generates correct plans that the orchestrator then mishandles. Fixing these 6 bugs should unblock all 3 scenarios without any model changes.

---

## Acceptance Criteria

### AC-1: Plan Step Preservation (P0)

**What**: LLM-generated plan steps must not be silently dropped. If the LLM returns N steps, N steps must execute (or a logged warning explains why a step was dropped).

**Root cause**: `_parse_plan_response()` in `planner/planner.py:458-465` silently drops steps when `ActionStep.__post_init__()` raises `ValueError` for unrecognized actions (e.g., `select`, `choose`, `fill`). The alias map in `shared_models.py:32-51` handles common misspellings but misses plausible LLM outputs. The truncation guard `_is_truncated_plan()` in `agent.py:1421-1432` only fires when the plan has zero interaction steps, so a plan missing one critical step passes unchecked.

**Requirements**:
1. Expand `_ACTION_ALIASES` to cover additional LLM-probable action names: `select` -> `click`, `choose` -> `click`, `fill` -> `type_text`, `submit` -> `click`, `navigate` -> `open_url`, `go_to` -> `open_url`, `find` -> `click`, `tap` -> `click`, `enter_text` -> `type_text`.
2. After parsing, assert `len(parsed_steps) >= len(raw_json["steps"])`. If steps were dropped, log a WARNING with the dropped step descriptions and the `ValueError` reason for each.
3. For any step whose action is still unrecognized after alias expansion, map it to `click` with the step's element/target as the element description (best-effort fallback) rather than dropping it silently.

**SOTA reference**: VerifyLLM pre-execution plan verification pattern — compare parsed plan against raw LLM output before execution ([SOTA doc, Section 1](walmart-return-fixes-sota.md#1-plan-step-dropping--truncation-detection)).

**Verification**: Unit test that feeds a plan with actions `["observe", "select", "fill", "click", "done"]` through `_parse_plan_response()` and asserts all 5 steps are present in the parsed output with correct action mappings.

---

### AC-2: Skill-Mandated Navigation (P0)

**What**: When a skill template specifies `open_url` in step 1, that navigation MUST execute regardless of current screen state. The planner must not skip skill-mandated navigation because the screen "already shows" the target page.

**Root cause**: The planner prompt (`plan_from_prompt.md:8-9`) injects the current screen description but contains no instruction to preserve skill-mandated steps. When the screen shows the Walmart orders page from a previous run, the LLM optimizes away navigation — treating the skill template as a suggestion, not a contract. The fallback plan in `_build_skill_fallback_plan()` at `agent.py:1448-1486` exists but only activates when `_is_truncated_plan()` fires (which requires zero interaction steps).

**Requirements**:
1. Add an explicit instruction to `plan_from_prompt.md`: "When a skill template specifies navigation steps (open_url, activate_app), you MUST include them in the plan even if the screen appears to already show the target page. The current screen state may be stale from a previous task. Skill navigation steps are a contract, not a suggestion."
2. In the orchestrator's `execute()` method, when a skill is matched and the skill's first step is `open_url` or `activate_app`, verify the generated plan includes that step. If missing, prepend it. This is a safety net independent of LLM compliance.

**SOTA reference**: Playwright always performs `page.goto()` even when the current URL matches. Enterprise RPA tools treat workflow steps as mandatory, not optimizable ([SOTA doc, Section 2](walmart-return-fixes-sota.md#2-stale-screen-state--navigation-skips)).

**Verification**: Unit test where the screen description says "Walmart orders page visible in Safari" and the skill specifies `open_url: https://www.walmart.com/orders`. Assert the final plan's first executable step is `open_url` with the correct URL.

---

### AC-3: wait_for_user Smart Skip (P1)

**What**: `wait_for_user` must detect when the expected page is already loaded (e.g., user is already logged in) and skip the wait, rather than blocking for 120s on screen-change detection that will never fire.

**Root cause**: Two-part bug. (A) `_extract_wait_condition()` at `agent.py:1789-1804` only matches the regex `^If\s+(.+?),\s*wait for...` but the LLM emits `"Please log in to Walmart"` which doesn't match. (B) `_wait_for_user()` at `agent.py:2296-2327` polls for a 2% pixel change every 5s for 120s. When already logged in, the page is static so no change is detected — full timeout is consumed. The condition-check early-exit path at lines 2257-2269 exists but is never reached because no condition is extracted.

**Requirements**:
1. Before entering the wait loop in `_wait_for_user()`, run an `observe` step to check whether the wait condition is already satisfied (e.g., "Is a login page currently displayed?"). If the expected page is already loaded (no login page visible), skip the wait immediately.
2. Make `_extract_wait_condition()` more robust: in addition to the existing `^If...` regex, extract conditions from common LLM phrasings like `"Please log in"` -> condition: `"login page is displayed"`, `"Please complete"` -> condition: `"completion form is displayed"`.
3. If the condition cannot be extracted, use a shorter timeout (30s instead of 120s) as a safety bound for unconditional waits.

**SOTA reference**: Skyvern's Validator agent checks action outcomes before proceeding; WebVoyager uses explicit `WAIT` actions with bounded timeouts ([SOTA doc, Section 3](walmart-return-fixes-sota.md#3-scroll-based-element-recovery), cross-ref Skyvern pattern).

**Verification**: Unit test where `_wait_for_user("Please log in to Walmart")` is called with a mock `coordinator.verify_condition()` that returns `True` (login page not needed). Assert the wait returns immediately without entering the polling loop.

---

### AC-4: Scroll Recovery Before Infeasibility (P1)

**What**: On NOT_FOUND for a click action, the agent must attempt at least 3 scroll-down actions before declaring infeasibility. The current behavior of aborting on the first NOT_FOUND with zero retries and zero scrolls must be eliminated.

**Root cause**: Three-part bug. (A) The infeasibility check at `agent.py:454-472` fires BEFORE `_handle_failure()` — on the very first NOT_FOUND for any click, it calls `_check_infeasibility(force=True)` and the LLM declares the task infeasible. Retries never get a chance. (B) `_vary_strategy()` at `agent.py:3023-3140` has no scroll-down strategy — it handles `scroll_to_top`, query refinement, and keyboard fallbacks, but never scrolls down to reveal off-screen elements. (C) `_parse_skill_steps()` at `agent.py:1521-1543` only parses `- verify:` lines; `- on_fail:` directives (like "scroll down to find it") are completely ignored.

**Requirements**:
1. Do NOT fire `_check_infeasibility(force=True)` on the first NOT_FOUND for a click step. Instead, allow the retry/scroll loop to execute first. Infeasibility should only trigger after retries are exhausted (i.e., after at least `max_scrolls` scroll attempts).
2. Add a `scroll_down_and_retry` strategy to `_vary_strategy()` that: (a) executes `scroll` action (half viewport down), (b) waits 1s for lazy-loaded content, (c) retries `find_element()` with the original element description. This should be the first strategy attempted on click NOT_FOUND.
3. Configure a minimum of 3 scroll attempts before infeasibility (configurable per-step via `on_fail` metadata, default 3).
4. Parse `on_fail:` directives from skill templates in `_parse_skill_steps()` and pass them as metadata on the generated plan steps so the orchestrator can use skill-specified recovery strategies.

**SOTA reference**: WebVoyager treats SCROLL as a first-class action with configurable targets. Playwright's `scrollIntoViewIfNeeded()` auto-scrolls before interaction. Skyvern's Validator triggers recovery on action failure ([SOTA doc, Section 3](walmart-return-fixes-sota.md#3-scroll-based-element-recovery)).

**Verification**: Integration test where `find_element()` returns NOT_FOUND for the first 3 calls, then returns a found element on the 4th call (after 3 scrolls). Assert the agent does not declare infeasibility and successfully clicks the element. Assert scroll actions are logged in the event stream.

---

### AC-5: Duplicate Skill Cleanup (P2)

**What**: No duplicate skill names in the registry. Delete stub files that conflict with the real skill template.

**Root cause**: Three files define Walmart return skills: `return_walmart_order.md` (real, 5 steps), `return-walmart-order.md` (stub, 1 step, `trusted: false`), and `return-walmart-order-2.md` (stub, 1 step). The registry at `skills/registry.py:178-195` loads files in `sorted()` order and silently overwrites on name collision. Currently the real skill loads last and wins, but this is fragile and produces a warning on every run.

**Requirements**:
1. Delete the two stub files: `src/automation_agent/skills/library/return-walmart-order.md` and `src/automation_agent/skills/library/return-walmart-order-2.md`.
2. Add a validation check in `load_from_directory()` that raises `ValueError` (or logs ERROR and skips the duplicate) when two files define the same `name:` field, rather than silently overwriting. This prevents future regressions.
3. Keep only `return_walmart_order.md` as the canonical skill file.

**Verification**: After deletion, run the skill registry loader and assert no "Duplicate skill name" warning is emitted. Unit test that loading two skill files with the same `name:` field raises an error or logs an ERROR.

---

### AC-6: Screenshot Persistence (P2)

**What**: Screenshots must be saved to the run log directory on every observe step, every NOT_FOUND event, and every step completion (post-action).

**Root cause**: Screenshots are captured in-memory (base64) throughout execution but `EventLogger.save_screenshot()` is only called from one place: Tier 2 vision verification in `verifier.py:142-149`. The observe handler at `agent.py:672-679` describes the screen but doesn't save the image. NOT_FOUND events save a debug crop (`_save_debug_image`) but not the full screenshot. Step execution doesn't save at all.

**Requirements**:
1. After every `observe` step, save the captured screenshot to the run log directory via `EventLogger.save_screenshot()`. Name format: `step_{NN}_observe.png`.
2. On every NOT_FOUND from `find_element()`, save the full screenshot (not just the debug crop). Name format: `step_{NN}_not_found_{element_desc_slug}.png`.
3. After each step execution completes (success or failure), save a post-action screenshot. Name format: `step_{NN}_post_{action}.png`.
4. Write the screenshot file path into the corresponding `events.jsonl` entry so the event log links to the visual state.
5. Gate the post-action screenshots behind a config flag (`AGENT_SAVE_STEP_SCREENSHOTS`, default `true`) so users can disable if disk space is a concern.

**SOTA reference**: Playwright Trace Viewer records screenshots at every action as a film strip. Selenium/BrowserStack auto-capture screenshots per command ([SOTA doc, Section 4](walmart-return-fixes-sota.md#4-screenshot-capture-during-automation)).

**Verification**: Unit test that mocks a 3-step execution (observe, click, done) and asserts `save_screenshot()` was called at least 3 times with correct name prefixes. Integration test that runs a short plan and verifies PNG files exist in the run log's screenshots directory.

---

## Out of Scope

- New features or new skill templates (fixing existing behavior only)
- New skills for other retailers
- Planner model changes (no model swaps, fine-tuning, or prompt rewriting beyond AC-2's navigation instruction)
- Config schema changes beyond the single `AGENT_SAVE_STEP_SCREENSHOTS` flag in AC-6
- Browser state reset between test scenarios (test harness concern, not agent concern)
- Infeasibility message improvements (distinguishing "wrong page" vs "feature absent") — valuable but not required for the 3 scenarios to pass
- Screen description text logging (instrumentation improvement, not blocking any scenario)

---

## Success Metrics

1. **Primary**: All 3 Walmart return customer test scenarios pass on re-test (up from 0/3).
   - Scenario 1 (happy path): Agent navigates to orders, finds the item, initiates return flow.
   - Scenario 2 (vague description): Agent navigates fresh, finds shoe-related order, proceeds.
   - Scenario 3 (error recovery): Agent navigates, finds dog food order, discovers non-returnable status, reports to user.

2. **Secondary**: No regressions in existing test suite (`pytest tests/unit/` all green).

3. **Instrumentation**: Post-mortem debugging is possible — screenshots exist in the run log directory for every step of every run.

---

## Implementation Order

Recommended sequencing based on dependency and impact:

| Phase | ACs | Rationale |
|-------|-----|-----------|
| 1 | AC-5 (duplicate cleanup) | Zero risk, removes a confounding variable before testing other fixes |
| 2 | AC-1 (step preservation) + AC-2 (navigation) | The two P0 bugs — must fix together because both block all scenarios |
| 3 | AC-4 (scroll recovery) | Unblocks element finding on long pages; depends on steps not being dropped (AC-1) |
| 4 | AC-3 (wait_for_user skip) | Reduces wasted time; independent of other fixes but lower priority |
| 5 | AC-6 (screenshots) | Observability — implement last so all other fixes are debuggable on re-test |

---

## Research References

| AC | SOTA Pattern | Source |
|----|-------------|--------|
| AC-1 | VerifyLLM pre-execution plan verification; step count assertion | [Grigorev et al., 2025](https://arxiv.org/html/2509.02761v2) |
| AC-1 | AgentSpec runtime enforcement DSL | [ICSE 2026](https://arxiv.org/abs/2503.18666) |
| AC-2 | Playwright force-navigation; RPA mandatory steps | [Playwright docs](https://playwright.dev/docs/navigations) |
| AC-2 | WebVoyager fresh context per task | [WebVoyager](https://arxiv.org/html/2401.13919v3) |
| AC-4 | WebVoyager SCROLL as first-class action | [WebVoyager](https://arxiv.org/html/2401.13919v3) |
| AC-4 | Skyvern Validator-driven recovery | [Skyvern](https://github.com/Skyvern-AI/skyvern) |
| AC-6 | Playwright Trace Viewer per-action screenshots | [Playwright Trace Viewer](https://playwright.dev/docs/trace-viewer) |
