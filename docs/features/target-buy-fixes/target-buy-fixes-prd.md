# PRD: Target Buy Fixes

**Date**: 2026-03-13
**Status**: DRAFT (R3 — addressing director + tech lead feedback)
**Author**: Product Manager (AI)
**Scope**: 9 gaps from "buy bed sheets on Target" customer testing

---

## 1. Problem Statement

Customer testing of the prompt "buy the top bed sheet cheaper than $50 on target" resulted in a **0/3 pass rate** across three scenarios. The agent cannot complete a purchase workflow on Target.com due to three classes of failure:

1. **Wrong-site routing**: The skill router matches `amazon-search` when the user explicitly says "on target," sending the agent to Amazon.com instead of Target.com (all 3 scenarios).
2. **Missing capability**: No Target shopping skill exists. The agent has no template for the navigate → search → filter → select → add-to-cart workflow on Target.com.
3. **Incomplete plans**: The planner generates 3-step plans (open URL → done) and treats displaying search results as task completion, never reaching product selection or add-to-cart.

Secondary issues compound the failures: keystrokes land on the wrong element, navigation verification produces false negatives, and scroll verification always fails.

Until these are fixed, the agent cannot fulfill any "buy X on [site]" request for Target — a core e-commerce automation use case.

---

## 2. Acceptance Criteria

### P0: Skill Routing — Site-Aware Matching

**AC-1**: When the user prompt contains an explicit site reference (e.g., "on target," "from target," "target.com"), the skill router must NOT return any skill whose `site`/`domain` metadata mismatches the extracted site entity — neither as a `direct` match nor as an `analogical` match. Wrong-site skills must be fully suppressed from the candidate list, not downgraded. Rationale: when the user explicitly names a site, returning a different-site skill (even as analogical) adds noise and risks the planner using its template. This applies to any current or future skill with site metadata (e.g., `amazon-search`, `buy-on-walmart`), not just Amazon-specific skills.

**AC-2**: When the user prompt contains "on target" (case-insensitive) and a `buy-on-target` skill exists, the router must return `buy-on-target` as the top match with confidence >= 0.6.

**AC-3**: When the user prompt contains no explicit site reference (e.g., "buy cheap bed sheets"), the router must fall through to existing LLM/keyword routing without site filtering — no regression to current behavior.

**AC-4**: Site entity extraction must be implemented as a **standalone pure function** with signature `extract_site_entities(prompt: str, known_sites: list[str]) -> list[str]` that is independently unit-testable. It must NOT be embedded in the router or registry — it must be importable and callable without instantiating any other component. The function must handle the following patterns and disambiguation rules:

- **Supported patterns**: "on {Site}", "from {Site}", "at {Site}", "{site}.com", and standalone site name when it matches a known site from the skills registry.
- **Known site list**: The canonical list of recognized sites must be derived from existing skill metadata — specifically, from skills that declare a `site` or `domain` field in their frontmatter, plus a hardcoded seed list of major e-commerce sites (target, amazon, walmart, bestbuy, ebay). The extractor does NOT attempt open-ended NER.
- **Disambiguation rules**:
  - "on {word}" is only extracted as a site if `{word}` matches a known site. "buy bedsheets on sale at target" must extract site="target" (from "at target"), NOT site="sale" (because "sale" is not a known site).
  - If the word following "on/from/at" is NOT a known site, the preposition-word pair is ignored and extraction continues scanning.
  - If multiple known sites appear in the prompt (e.g., "buy sheets from amazon and target"), the extractor must return ALL matched sites. However, the router must treat multiple extracted sites as ambiguous: if more than one site is extracted, the router must return no match and let the planner generate a plan from scratch. Rationale: multi-site prompts are rare and ambiguous (compare prices? buy on both?). Returning skills for multiple sites adds complexity without clear user intent. The planner can handle the ambiguity via its LLM reasoning.
  - **Standalone site name without a preposition** (e.g., "target bed sheets") requires extra disambiguation because the site name may be part of the product (e.g., "buy target gift card" — "target" is the product brand, not the shopping site). Rules:
    - A standalone known-site word is only extracted as a site entity if it appears as a standalone token AND is NOT immediately followed by a noun that forms a product compound (e.g., "target gift card" → "target" modifies "gift card", not a site reference).
    - **Preposition-anchored matches always take priority**: "on target", "from target", "at target" are unambiguous site references regardless of surrounding words. "buy target gift card on target" → site="target" (from "on target"), product="target gift card".
    - **Standalone-only extraction is disabled by default**. The extractor must require a preposition anchor ("on/from/at {site}" or "{site}.com") to extract a site. A bare "target bed sheets" with no preposition does NOT extract a site — the router falls through to LLM/keyword routing which can infer intent from context. This eliminates false positives like "buy target gift card" extracting site="target".
    - Rationale: preposition anchors are high-precision signals; standalone brand names are ambiguous. The cost of missing a site extraction (falling to LLM routing) is low; the cost of a false extraction (filtering out the correct skill) is high.
- Must be case-insensitive and handle possessive forms (e.g., "Target's" → "target").

**AC-4b**: When site extraction identifies a site but NO skill exists for that site, the router must return no match (not a different-site skill). The planner must then generate a plan from scratch without a skill template. This ensures fixing P0-1 (routing) is not blocked by P0-2 (skill creation) — the system degrades gracefully when a skill is missing rather than routing to the wrong site.

### P0: Target Shopping Skill

**AC-5**: A skill file `buy_on_target.md` must exist in `src/automation_agent/skills/library/` with valid YAML frontmatter including: `name`, `skill-id`, `description`, `trigger-keywords`, `parameters` (at minimum `product` as required string), `requires` (apps: [Safari, Google Chrome], os: darwin), and `success-condition`. The `requires.apps` field must list both Safari and Google Chrome because customer testing showed Chrome was used in scenarios 1 and 3, and Safari in scenario 2. The agent must work with whichever browser the user has active.

**AC-6**: The skill must define at least 5 numbered steps (using the existing skill step format: `N. action: description` + `- verify: condition`) covering the full buy workflow: (1) navigate to target.com, (2) search for product, (3) apply filters or sort by price, (4) select a product, (5) add to cart. Each step must have a `verify` condition. The skill must include a final `done` step with an explicit stop message: "Product added to cart. Checkout requires user confirmation." The skill must NOT include steps to complete checkout or enter payment (out of scope).

**AC-7**: The skill's `trigger-keywords` must include Target-specific terms ("target", "target.com") alongside generic shopping terms ("buy", "purchase", "shop", "add to cart"). Keywords must NOT include competitor site names ("amazon", "walmart"). To prevent false matches on generic prompts with no site specified (e.g., "buy cheap bed sheets"), the skill's keyword matching must require at least one Target-specific keyword ("target" or "target.com") to be present in the prompt for keyword-fallback matching. Generic shopping keywords alone ("buy", "shop") must NOT be sufficient to match this skill via keyword fallback. LLM-based routing may still match if it determines Target intent from context, but keyword fallback must not.

### P0: Plan Depth — Full Buy Workflow

**AC-8**: When the planner receives a "buy X on [site]" prompt and a matching skill template exists, the generated plan must contain **at least 3 interaction steps** (as defined below). Specifically, the plan must include at least: (1) one `click` or `type_text` step for searching/filtering, (2) one `click` step for product selection, and (3) one `click` step targeting an "Add to Cart" or equivalent button, with a verify condition that confirms the cart was updated. A plan that ends after `open_url` or after displaying search results is truncated.

**Testability**: Given a plan, count steps where `action in ("click", "type_text", "scroll")`. If count < 3 and the goal contains a "buy" intent keyword, the plan is truncated. This is a deterministic, unit-testable check.

**Definition of "interaction step"** (applies to AC-8 and AC-10): An interaction step is any `ActionStep` whose `action` field is `click`, `type_text`, or `scroll`. Actions `open_url`, `activate_app`, `press_key`, `wait`, and `done` are NOT interaction steps. This aligns with the existing `_is_truncated_plan` implementation at `agent.py:1465` which checks for `click` and `type_text`.

**AC-9**: The planner system prompt must include explicit goal-completion criteria for e-commerce "buy" tasks: opening a search URL is NOT task completion; the task is complete when a product has been added to the cart and the cart update is verified.

**AC-10**: A plan is truncated when it meets ALL of the following: (1) the goal contains a buy-intent keyword ("buy", "purchase", "order", "add to cart"), (2) the plan contains fewer than 3 interaction steps, and (3) a matched skill template contains 3 or more interaction steps. When detected as truncated, the system must replace the LLM-generated plan with the skill-based fallback plan built from the skill template. This mechanism already exists (`_is_truncated_plan` + `_build_skill_fallback_plan`) but must be updated to use the >= 3 interaction step threshold and must work correctly with the new `buy_on_target` skill.

### P1: type_text Element Focus

**AC-11**: Before executing `type_text`, the actuator dispatch must focus the target element. If the `type_text` action includes an `element` parameter describing the target field, the system must call `find_element` to locate it, `click` at the returned coordinates, wait at least 200ms, and then send keystrokes. The `screenshot_b64` variable used in the `find_element` call must be defined in scope (fix the existing `NameError` bug in `_dispatch_action` at `agent.py:2110-2111` by capturing a screenshot at the top of the type_text branch, the same way `_find_element()` does it at line 2195).

**AC-11b**: If `find_element` returns `None` (element not found) or returns a result with confidence below the gating threshold (0.5), the system must: (1) log a warning including the element description and the confidence value (if any), and (2) fall through to typing to the currently focused element. The step must NOT be failed — verification will catch incorrect typing downstream. Rationale: failing the step entirely is worse than typing to the wrong place, because the verifier can detect and trigger a replan, whereas a hard failure skips verification entirely.

**AC-12**: If `type_text` has no `element` parameter, behavior must remain unchanged (keystrokes sent to currently focused element) — no regression.

### P1: open_url False Negative

**AC-13**: For `open_url` actions, the screenshot-diff check must NOT override `actuator_result["success"]` to `False`. When `step.action == "open_url"` and `visible_effect` is False, the diff result must be stored as metadata (`actuator_result["_no_visible_change"] = True`) but must not set `success = False` or set an `error` message. This allows the existing Tier 1 URL verification (at line 1122) to run and make the authoritative pass/fail decision based on the actual browser URL.

**Implementation constraint**: In the diff-check block at `agent.py:1059-1081`, add a guard: `if step.action == "open_url": store metadata and skip the success override`. The `click` action diff check remains unchanged. The `_no_visible_change` metadata is available to downstream logging and diagnostics but does not gate verification.

### P1: Scroll Verification

**AC-14**: Scroll actions must use a tiered verification chain, tried in order:

1. **Tier S1 — JavaScript scrollY delta** (PRIMARY, browser contexts only): Before the scroll action, query `window.scrollY` via `do JavaScript` in Safari or equivalent in Chrome. After scroll, query again.
   - **Safari**: `tell application "Safari" to do JavaScript "window.scrollY" in current tab of front window`
   - **Chrome**: `tell application "Google Chrome" to execute front window's active tab javascript "window.scrollY"`
   - **Verdict logic**:
     - If delta > 0 (scroll-down) or delta < 0 (scroll-up): scroll **succeeded**. This is authoritative — do NOT consult Tier S2 or vision model.
     - If delta = 0: scroll is **inconclusive** (not failed). The page may be at the top/bottom boundary, or the scroll target may be a nested scrollable element whose scrollY isn't captured by `window.scrollY`. Fall through to Tier S2.
   - If the frontmost app is NOT a browser (e.g., Finder, System Settings), skip to Tier S2.
   - If `do JavaScript` raises an error (e.g., page blocks JS execution, permission denied), skip to Tier S2.

2. **Tier S2 — Screenshot pixel-diff** (SECONDARY): Compare pre-scroll and post-scroll screenshots using `screenshot_diff.screen_changed()`. This differs from the current broken behavior because: (a) it is used as a PASS signal (pixel change detected = scroll succeeded), not handed to the vision model for interpretation, and (b) it requires capturing the pre-scroll screenshot BEFORE dispatch (the current code doesn't do this for scroll). If pixel change exceeds a threshold (>1% of pixels changed), scroll **succeeded**. If pixel change is below threshold, scroll is **inconclusive** — fall through to Tier S3.

3. **Tier S3 — Actuator success** (FALLBACK): If both Tier S1 and S2 are unavailable or inconclusive, accept the actuator's return value. The actuator's `scroll()` method returns `{"success": True}` if the OS-level scroll event was dispatched. This is the weakest signal but avoids the current failure mode of always-deny. Scroll is reported as **succeeded** with a log note indicating actuator-only verification.

**Precedence**: Tier S1 > Tier S2 > Tier S3. A higher-tier success is authoritative and skips lower tiers. Inconclusive results fall through. Vision-model verification of abstract conditions like "content shifted downwards" must NOT be used as the sole or primary verification mechanism for scroll steps.

### P2: Duplicate Walmart Skills

**AC-15**: The files `return-walmart-order.md` and `return-walmart-order-2.md` must be deleted from `src/automation_agent/skills/library/`. Only the canonical `return_walmart_order.md` must remain. No "Duplicate skill name" error must appear in logs during skill loading.

### P2: Skill Librarian Activation

**AC-16**: The Skill Librarian must be invocable after execution runs that include learning-worthy traces. This requires three changes:

**(a) Enable by default with graceful degradation**: `skill_librarian_enabled` must default to `True` in config. If `experience_store` is `None` (user hasn't configured a store), the system must silently skip librarian evaluation with a debug-level log — NOT crash. The librarian is only instantiated when both the flag is True AND an experience store exists (per existing guard at `registry.py:85-99`); the flag change alone does not introduce new failure modes.

**(b) Lower promotion thresholds for first-run learning**: The following thresholds must be changed:
- `skill_librarian_min_observations`: 5 → 2
- `skill_librarian_min_runs`: 3 → 1
- `skill_librarian_min_confidence`: 0.7 → 0.5

**CRITICAL — confidence dead zone fix**: The `min_confidence` change is mandatory, not optional. Replan runs cap observation confidence at 0.6 (registry.py:614-616 applies a `REPLAN_CONFIDENCE_CAP = 0.6`). With the current `min_confidence=0.7`, replan-generated observations can NEVER be promoted because `0.6 < 0.7`. Since replan runs are exactly the runs that produce the most valuable learning observations (they contain recovery patterns), this dead zone means the librarian's most useful input is permanently blocked. Setting `min_confidence=0.5` ensures replan observations (capped at 0.6) clear the threshold while still filtering out very low-quality observations. The existing Bayesian scoring (`_compute_score`) provides additional quality gating beyond the threshold.

**(c) Learning trigger broadened**: `_trace_deserves_learning` must return `True` when the trace contains at least one replan, retry, or step failure followed by recovery. A perfectly clean run (zero retries, zero replans) may still return `False` — that is acceptable because clean runs produce no novel observations worth learning from.

### P2: Domain Verification

**AC-17**: When a skill or prompt specifies a target domain, the verifier must check that the browser's current URL domain matches the expected domain after `open_url` actions. A navigation to `amazon.com` when the expected domain is `target.com` must fail verification regardless of URL token matches.

**How the expected domain reaches the verifier**: The orchestrator must inject the expected domain into the `ActionStep.verify` text before passing it to the verifier. Specifically: when a skill is matched and the skill has a `domain` or `site` field in its frontmatter (e.g., `site: target.com`), the orchestrator must append ` AND browser domain is {site}` to each `open_url` step's verify field. If no skill is matched but the site entity extractor (AC-4) identified a site from the prompt, the orchestrator must use that site's canonical domain (e.g., "target" → "target.com") for the same injection. This approach requires no changes to the verifier's interface — it operates entirely through existing verify-text processing.

**AC-18**: Domain verification must normalize URLs: strip `www.` prefix, ignore protocol (http/https), and match base domain (e.g., `shop.target.com` matches expected domain `target.com`). Subdomain matching must use suffix comparison: the actual domain must end with the expected domain (so `shop.target.com` matches `target.com` but `nottarget.com` does not).

**Implementation note**: Domain verification must NOT reuse the existing `_url_tokens()` method from `verifier.py`. That method mixes host and path tokens without distinguishing them — which is exactly the root cause of the P2-3 bug (URL tokens from amazon.com path matched when they shouldn't have). Domain comparison must use dedicated domain-extraction logic: `urlparse(url).netloc` → strip `www.` → suffix match against expected domain. This is a separate code path from `_url_tokens()`.

### Cross-Cutting: Regression Safety

**AC-19**: All existing unit and integration tests (currently 522 passing) must continue to pass after all changes land. Zero regressions allowed. Specifically:
- Existing Amazon search scenarios must still work when the prompt does NOT mention a specific non-Amazon site (AC-3 covers this, AC-19 requires test evidence).
- Existing Walmart return skill (`return_walmart_order.md`) must still load and match correctly after the duplicate stubs (AC-15) are deleted.
- All existing test files listed in the codebase analysis must pass: `test_router_v2.py`, `test_verifier.py`, `test_planner.py`, `test_skill_librarian.py`, `test_scroll_action.py`, `test_scroll_replan_integration.py`, `test_vision_arch_improvements.py`.
- **Routing regression validation**: New tests must verify that the site extraction filter does not alter routing scores or behavior for prompts that do NOT trigger site extraction (i.e., prompts with no preposition-anchored site reference). Specifically: the Bayesian scorer output and keyword-fallback confidence for existing Amazon/Walmart prompts must be identical before and after the site extraction feature lands. This validates that the pre-filter is truly a no-op when it doesn't match.
- **No test deletion**: No existing test may be deleted or modified to accommodate new behavior unless the test was explicitly testing incorrect behavior. Any such modification requires a justification comment in the test file explaining why the old assertion was wrong.

---

## 3. Out of Scope

- **Full checkout/payment flow**: The skill and planner must stop at add-to-cart. We are NOT building checkout automation, payment entry, or order placement.
- **New LLM providers or vision models**: No changes to the model backend. Fixes use existing Molmo/Gemma2/Gemini infrastructure.
- **Generic e-commerce platform**: We are building a Target-specific skill, not a universal shopping agent. Other sites (Walmart, Best Buy) are future work.
- **Login automation**: The agent must detect login walls and defer to the user (existing `wait_for_user` pattern), not automate login.
- **Semantic router / embedding-based routing**: The SOTA research identified semantic routing (Aurelio Labs) as a future option for 50+ skills. We are using entity extraction pre-pass, which is simpler and sufficient for the current skill count.
- **Non-browser scroll verification via JavaScript**: AC-14's JS-based scroll check only applies to browser contexts (Safari, Chrome). Scroll in native apps (Finder, Settings) falls back to Tier S2/S3.

---

## 4. Success Metrics

| Metric | Current | Target | How to Measure |
|--------|---------|--------|----------------|
| **Customer test pass rate** | 0/3 (0%) | 3/3 (100%) | Re-run scenarios 1-3 from `target-buy-bedsheets-customer-scenarios.md` |
| **Skill routing accuracy** | 0/3 correct | 3/3 correct | All three scenarios must match `buy-on-target` (not `amazon-search`) |
| **Plan depth** | 3 steps (open URL → done) | >= 5 steps through add-to-cart | Count steps in generated plans from run logs |
| **type_text focus accuracy** | Text went to wrong tab | Text goes to intended element | Screenshots confirm text in correct search bar |
| **Scroll verification** | 0/4 scroll verifications passed | >= 3/4 pass | Check verification verdicts in run logs |
| **Skill adaptation** | 0 skills created after 3 runs | >= 1 new skill promoted after 2 runs | Check `skills/library/` for new files after execution. With lowered thresholds (AC-16b: min_observations=2, min_runs=1, min_confidence=0.5), promotion is possible after a single replan run (replan observations are capped at 0.6, which now clears the 0.5 threshold). The metric conservatively says "after 2 runs" to account for the possibility that a single run produces only 1 observation. |
| **Scenarios 4-5 unblocked** | Not executable | Executable (may still fail on their own merits) | Attempt scenarios 4-5 after P0 fixes land |
| **Regression** | 522/522 tests pass | 522/522 tests pass (plus new tests) | `pytest tests/unit/ tests/integration/` — zero failures |

### Unit-Testable Proxy Metrics

The e2e customer test metrics (3/3 pass rate) require a live macOS desktop, Target.com access, and running vision models — they cannot be part of CI. The following unit-testable proxies must pass in CI as evidence that the e2e metrics will be met:

| P0 Issue | Unit Test | Expected Result |
|----------|-----------|-----------------|
| **P0-1 (routing)** | `extract_site_entities("buy the top bed sheet on target", known_sites)` | Returns `["target"]` |
| **P0-1 (routing)** | Given skills `[amazon-search, buy-on-target]` and extracted site `"target"`, router candidate list | Must contain only `buy-on-target`; `amazon-search` fully suppressed |
| **P0-1 (no false positive)** | `extract_site_entities("buy cheap bed sheets", known_sites)` | Returns `[]` (empty) |
| **P0-1 (disambiguation)** | `extract_site_entities("buy target gift card", known_sites)` | Returns `[]` (no preposition anchor) |
| **P0-2 (skill valid)** | `registry.validate_all()` after loading `buy_on_target.md` | Zero errors |
| **P0-3 (truncation)** | `_is_truncated_plan(plan_with_1_interaction, skill_with_5_interactions)` | Returns `True` |
| **P0-3 (not truncated)** | `_is_truncated_plan(plan_with_4_interactions, skill_with_5_interactions)` | Returns `False` |
| **P1-2 (open_url)** | Dispatch `open_url` with `visible_effect=False` | `actuator_result["success"]` NOT overridden; `_no_visible_change` metadata set |
| **P1-3 (scroll)** | Scroll with scrollY delta > 0 | Scroll verified as succeeded without vision model |
| **P2-3 (domain)** | Verify text contains "browser domain is target.com", actual URL is amazon.com | Verification fails |

---

## 5. Research References

### Skill Routing (P0-1)
- **Entity extraction pre-pass**: Recommended by SOTA as the simplest deterministic fix. Pattern: extract site entities via regex ("on {site}", "from {site}"), use as hard constraint to filter candidate skills before LLM/keyword scoring. (SOTA §P0-1, "Entity Extraction Pre-Pass")
- **Codebase analysis**: Router prompt at `skills/prompts/route_skill.md` lacks site-awareness instruction. `_parse_response()` at `router.py:172-243` has no site-mismatch check. Fix options include prompt fix + post-filter in registry. (Codebase §P0-1)
- **Disambiguation**: SOTA §P0-1 "Common Failure Modes" identifies keyword overlap and missing skills as key failure modes. AC-4's disambiguation rules and AC-4b's graceful degradation directly address these.

### Target Shopping Skill (P0-2)
- **Skyvern purchase workflow pattern**: Structured phases (navigate → search → filter → select → add-to-cart → checkout) with verification at each phase. (SOTA §P0-2, "Skyvern's Purchase Workflow Pattern")
- **Emergence AI domain insights**: Pre-computed structural knowledge about Target.com reduces LLM calls by 50%. Maps to our skill file format where steps describe expected UI patterns. (SOTA §P0-2)
- **Codebase analysis**: Skill file format documented at `skills/loader.py`. `amazon_search.md` and `return_target_order.md` provide structural templates. (Codebase §P0-2)

### Plan Depth (P0-3)
- **Agent-E hierarchical planner-navigator**: Two-tier architecture achieving 73.2% success on WebVoyager (16% above prior SOTA). Planner decomposes into sub-goals with verification at each. (SOTA §P0-3, "Hierarchical Planner-Navigator Architecture")
- **Goal-completion criteria**: Explicit completion rules in planner prompt prevent premature task completion. (SOTA §P0-3, "Goal-Completion Criteria in System Prompt")
- **Codebase analysis**: `_is_truncated_plan()` and `_build_skill_fallback_plan()` already exist at `agent.py:1460-1546` but only work when the matched skill has interaction steps. Definition of interaction steps aligned with existing code at `agent.py:1465`. (Codebase §P0-3)

### type_text Focus (P1-1)
- **Click-to-focus pattern**: Universal macOS UI automation pattern — click target element before keystroke. (SOTA §P1-1)
- **Codebase analysis**: Bug identified — `screenshot_b64` is undefined in `_dispatch_action()` scope at `agent.py:2110-2111`. The click-to-focus code path exists but crashes with `NameError`. (Codebase §P1-1)

### open_url Verification (P1-2)
- **URL-based verification**: For `open_url`, success criterion is "browser shows requested URL" — checkable deterministically via AppleScript without screenshots. (SOTA §P1-2, "URL-Only Verification for open_url")
- **Codebase analysis**: The diff check at `agent.py:1077-1081` overrides `actuator_result["success"]` BEFORE Tier 1 URL verification runs, preventing the correct URL check from executing. AC-13 specifies inline URL check before override rather than reordering the verification pipeline. (Codebase §P1-2)

### Scroll Verification (P1-3)
- **JavaScript scrollY delta check**: Measure `window.scrollY` before and after scroll via `do JavaScript` in Safari. Deterministic, fast (~10ms), no vision model needed. (SOTA §P1-3)
- **Chrome support**: Chrome's AppleScript API uses `execute ... javascript` instead of `do JavaScript`. AC-14 Tier S1 specifies both browser APIs. (SOTA §P1-3, "JavaScript disabled" fallback)
- **Codebase analysis**: Tier 1 verifier returns `None` (inconclusive) for scroll actions, falling through to Tier 2 vision verification which always fails for abstract scroll conditions. AC-14's tiered chain replaces this with deterministic signals. (Codebase §P1-3)

### Skill Librarian (P2-2)
- **Emergence AI self-improvement**: Auto-generates navigational insights from past executions, stored as long-term memory. (SOTA §P2-2)
- **Codebase analysis**: `skill_librarian_enabled` defaults to `False` (config.py:198). Librarian only created when flag is True AND experience_store exists (registry.py:85-99). Thresholds (min_observations=5, min_runs=3) prevent first-run learning. `_trace_deserves_learning` (registry.py:661-671) skips clean runs. AC-16 addresses all three blockers. (Codebase §P2-2)

### Domain Verification (P2-3)
- **AGrail guardrail pattern**: Safety checks should verify "action alignment with intended tasks" — domain verification is a specific instance. (SOTA §P2-3, citing ACL 2025)
- **Codebase analysis**: `_url_tokens()` at `verifier.py:230-242` extracts tokens from both host and path without distinguishing them. The verifier doesn't have access to the original goal/prompt — AC-17 specifies injecting expected domain into verify text at the orchestrator level, avoiding verifier interface changes. (Codebase §P2-3)
