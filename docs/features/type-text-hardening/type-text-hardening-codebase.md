# Type-Text-Hardening: Codebase Research

## 1. Relevant Code with File:Line References

### 1.1 Skill Compiler (`_compile_skill_instruction`)

**File:** `src/automation_agent/orchestrator/agent.py:1730-1926`

The compiler translates natural-language skill instructions into executable `ActionStep` objects. Key patterns handled:

| Pattern | Lines | Output Action |
|---------|-------|---------------|
| `Navigate to <URL>` | 1743-1771 | `open_url` with URL encoding |
| `Navigate to <text>` | 1772 | `click` via `_make_click_step` |
| `Click on/the <element>` | 1870-1876 | `click` |
| `Find X and click "Y"` | 1853-1860 | `click` |
| `Type "X" and press Enter` | 1878-1909 | `type_text` + `press_key` (2 steps) |
| `Type "X" in the <field> and press Enter` | 1878-1909 | `type_text` (with element) + `press_key` |
| `Press <keys>` | 1911-1924 | `press_key` |
| `done` / `complete task` | 1741-1742 | `done` |
| `wait for user` | 1773-1800 | `wait_for_user` |

**URL encoding logic (lines 1748-1761):**
When the destination starts with `https?://`, the compiler:
1. Parses with `urlparse`
2. If query params exist, re-encodes them via `parse_qs` + `urlencode` (handles spaces)
3. Elif spaces exist in URL, does naive `replace(" ", "%20")`
4. Falls through on parse errors with `replace(" ", "%20")`

**type_text compilation (lines 1878-1909):**
The regex `^Type\s+"?(.+?)"?\s+(?:in the (.+?)\s+)?(?:and|then)\s+press\s+Enter$` captures:
- Group 1: the text to type
- Group 2 (optional): the element/field description
- If no element specified, defaults to `"search or text input field"` (line 1891)
- Always produces TWO ActionSteps: `type_text` then `press_key(return)`

### 1.2 Type Text Dispatch (`_dispatch_action`)

**File:** `src/automation_agent/orchestrator/agent.py:2201-2255`

The click-to-focus flow for `type_text`:
1. Pops `element` from params (line 2203)
2. If element exists and `_skip_focus` is not set:
   - Captures screenshot (line 2207) -- was previously a NameError bug (P1-1)
   - Calls `coordinator.find_element(element_desc)` (line 2208-2209)
   - If location found, clicks at that position (line 2230)
   - No confidence gating for click-to-focus (line 2216-2219) -- intentional: clicking wrong element is recoverable, not clicking is worse
   - If not found, logs warning and types to current focus (line 2234-2239)
3. Optional `_clear_first`: sends Cmd+A before typing (line 2247-2251)
4. Optional `_slow_type`: character-by-character typing (line 2252-2253)
5. Calls `actuator.type_text(text)` (line 2255)

### 1.3 Domain Injection (`_inject_domain_verification`)

**File:** `src/automation_agent/orchestrator/agent.py:1520-1548`

Called during plan generation (line 354) and replanning (line 3606).

Flow:
1. `extract_site_entity(goal)` extracts site names from the user prompt (line 351)
2. If exactly one site found, `_expected_domain` is set to `{site}.com` (line 353)
3. For each `open_url` step in the plan:
   - **PB7 guard (line 1532-1534):** Only injects when step URL contains the expected domain. Prevents poisoning non-target URLs in multi-domain plans.
   - Appends ` AND browser domain is {domain}` to `step.verify` (line 1536-1538)
   - Idempotent: checks for existing `"browser domain is"` marker (line 1535)

### 1.4 Domain Verification in Verifier

**File:** `src/automation_agent/orchestrator/verifier.py:395-414`

Within `_verify_tier1` for `open_url` steps:
1. Regex extracts expected domain from verify text: `browser domain is (\S+)` (line 398-399)
2. Extracts actual domain from browser URL via `_extract_base_domain()` (line 405)
3. Checks exact match OR subdomain match (`.endswith(f".{expected}")`) (line 406-408)
4. Fails fast before URL token matching if domain mismatch (line 397 comment)

### 1.5 Site-Aware Routing (`extract_site_entity`)

**File:** `src/automation_agent/skills/router.py:58-75`

Preposition-anchored pattern matching. Two regex patterns (built by `_build_site_patterns`):
1. `\b(?:on|from|at)\s+(SITES)(?:'s)?\b` -- catches "on target", "from amazon", "at walmart"
2. `\b(SITES)\.com\b` -- catches "target.com"

Returns sorted list of matched sites or `None`. Multiple sites = ambiguous = no skill match.

**Known sites** (`_SEED_SITES`, line 27-29): amazon, target, walmart, bestbuy, ebay, costco, etsy, newegg. Supplemented at runtime by skill metadata `site` field.

### 1.6 Required-Keywords Gate (`match_skill`)

**File:** `src/automation_agent/skills/matcher.py:42-48`

Word-boundary matching using `\b` regex anchors:
```python
re.search(r"\b" + re.escape(rk) + r"\b", prompt_lower)
```
This prevents substring false positives (PB4): "retarget" and "untargeted" do NOT match "target".

### 1.7 Scroll Verification (Tier S1)

**File:** `src/automation_agent/orchestrator/verifier.py:476-513`

Three-tier scroll verification:
- **S1 (lines 480-498):** JS scrollY delta. Captures `get_scroll_position()` before and after scroll. Checks `delta > 0` for down, `delta < 0` for up. Delta=0 falls through.
- **S2 (lines 500-506):** Screenshot pixel diff via `_scroll_pixel_changed` metadata.
- **S3 (lines 508-513):** Actuator success fallback (least reliable).

### 1.8 Buy-on-Target Skill

**File:** `src/automation_agent/skills/library/buy_on_target.md`

Key design decisions:
- Step 1 uses direct search URL: `Navigate to https://www.target.com/s?searchTerm={{product}}` -- avoids fragile type_text-in-search-bar flow
- Has `required-keywords: [target, target.com]` metadata to prevent cross-site activation
- Has `site: target` metadata for site-aware filtering

---

## 2. PB8: Horizontal Scroll Dead Code Analysis

### The Bug

**Location:** `src/automation_agent/orchestrator/verifier.py:476-497`

The scroll verification Tier S1 only checks vertical scroll directions (`"down"` and `"up"`). Horizontal scrolls (`"left"` and `"right"`) are never matched by the S1 checks, causing them to silently fall through.

### Dead Code Trace

```python
# verifier.py:476-497
if step.action == "scroll":
    direction = step.params.get("direction", "down")  # line 477
    scroll_before = actuator_result.get("_scroll_y_before")  # line 478

    # Tier S1: JS scrollY delta
    get_scroll = getattr(actuator, "get_scroll_position", None)  # line 481
    if get_scroll is not None and scroll_before is not None:  # line 482
        scroll_after = get_scroll()  # line 483
        if scroll_after is not None:  # line 484
            delta = scroll_after - scroll_before  # line 485
            if direction == "down" and delta > 0:   # line 486 -- ONLY vertical
                return (True, ...)
            if direction == "up" and delta < 0:     # line 492 -- ONLY vertical
                return (True, ...)
            # delta == 0 falls through to S2    # line 498
```

**Why horizontal scrolls are dead code in S1:**
1. `direction` is `"left"` or `"right"` for horizontal scrolls
2. Lines 486 and 492 only match `"down"` and `"up"`
3. Even if `get_scroll_position` returns data, horizontal scrolls skip BOTH checks
4. They fall through with any delta (not just delta=0), reaching S2 or S3

**Additionally:** the `_scroll_y_before` metadata is always captured for scrolls (agent.py:2283-2287, 2309-2310), but for horizontal scrolls, the Y position is irrelevant -- scrollX should be captured instead.

### Dispatch Side (agent.py:2276-2315)

The dispatch correctly differentiates horizontal/vertical scrolls:
```python
# agent.py:2293-2300
if direction in ("left", "right"):
    clicks = amount if direction == "right" else -amount
    result = self.actuator.scroll(clicks, x=..., y=..., horizontal=True)
else:
    result = self.actuator.scroll(clicks, x=..., y=...)
```

But the verification metadata is vertical-only:
```python
# agent.py:2309-2310 -- always stores scrollY, never scrollX
if scroll_before is not None:
    result["_scroll_y_before"] = scroll_before
```

### Fix Required

1. **Verifier S1:** Add `"left"`/`"right"` direction checks using scrollX delta
2. **Dispatch metadata:** Capture and store `_scroll_x_before` for horizontal scrolls
3. **Actuator:** May need `get_scroll_position_x()` or `get_scroll_position(axis="x")`

---

## 3. Patterns to Follow

### 3.1 Test Helper Pattern

All test files use a consistent `_make_config()` helper to avoid `.env` leaking `model_provider`:
```python
def _make_config(**overrides) -> AgentConfig:
    defaults = {
        "_env_file": None,
        "anthropic_api_key": "test-key-not-real",
        "model_provider": "local",
        "grounding_model": "",
        "grounding_server_url": "",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)
```

### 3.2 Agent Construction in Tests

Mock all protocol-implementing components:
```python
planner = AsyncMock()
skill_registry = MagicMock()
skill_registry.match = AsyncMock(return_value=None)
skill_registry.learn_from_run = AsyncMock(return_value=[])
skill_registry.promote_from_run = AsyncMock(return_value=None)
coordinator = AsyncMock()
coordinator.capabilities = MagicMock(return_value=frozenset())
coordinator.capture_screenshot = AsyncMock(return_value="base64data")
actuator = MagicMock()
```
See `test_site_routing.py:585-605` and `test_target_buy_fixes.py:94-139`.

### 3.3 Verifier Testing Pattern

Verifier can be tested standalone (`test_verifier.py:363-394`):
```python
verifier = StepVerifier(actuator=mock_act, coordinator=mock_coord, logger=logger)
result = await verifier.verify(step, actuator_result)
```

For Tier 1 only (synchronous, no coordinator needed):
```python
result = verifier._verify_tier1(step, actuator, actuator_result)
# Returns Optional[Tuple[bool, str]]
```

### 3.4 ActionStep Construction

Always provide `verify` (mandatory for non-terminal actions). Minimal:
```python
ActionStep(action="scroll", params={"direction": "down", "amount": 3}, verify="Page scrolled down")
```

### 3.5 Skill Instruction Compilation Testing

Test via `agent._compile_skill_instruction(instruction, verify)` which returns `Optional[list[ActionStep]]`.

---

## 4. Integration Points

### 4.1 User Prompt Flow: "buy bed sheets on target"

```
1. agent.execute("buy bed sheets on target")
   |
2. skill_registry.match(goal) -> SkillMatchResult
   |-- extract_site_entity(goal) -> ["target"]
   |-- _filter_by_site(candidates, "target") removes wrong-site skills
   |-- Returns skill_context with expanded steps
   |
3. planner.plan(goal, screen_context, skill_context) -> ActionPlan
   |
4. _is_truncated_plan(plan, fallback_plan) -> may replace with fallback
   |
5. _inject_domain_verification(plan, "target.com")
   |-- Appends "AND browser domain is target.com" to open_url verify fields
   |
6. For each step in plan:
   |-- _dispatch_action(step) -> actuator_result
   |   |-- For type_text: find_element + click-to-focus + type
   |   |-- For scroll: capture scrollY before, dispatch, store metadata
   |   |-- For open_url: dispatch, check screenshot diff
   |
   |-- verifier.verify(step, actuator_result) -> StepResult
       |-- Tier 0: Accessibility (type_text text match)
       |-- Tier 1: Actuator state (domain check, app check, scrollY delta)
       |-- Tier 2: Vision screenshot verification
```

### 4.2 Component Boundaries

| From | To | Interface | Notes |
|------|----|-----------|-------|
| Agent | Coordinator | `find_element(desc, screenshot_b64=)` | Returns `FindElementResult` |
| Agent | Actuator | `click(x, y)`, `type_text(text)`, `scroll(clicks, horizontal=)` | Returns `dict` |
| Agent | Verifier | `verify(step, actuator_result)` | Returns `StepResult` |
| Verifier | Actuator | `get_state()`, `get_scroll_position()` | Synchronous calls |
| Verifier | Coordinator | `verify_condition(text, screenshot_b64=)` | Async, Tier 2 |
| Router | Skills | `extract_site_entity(prompt)` | Pure function |
| Matcher | Skills | `match_skill(prompt, skills)` | Keyword fallback |

### 4.3 Replan Domain Injection

When replanning occurs (agent.py:3605-3606), `_inject_domain_verification` is called again on the new plan using the stored `_expected_domain`. This ensures domain constraints persist across replans.

---

## 5. Test Infrastructure

### 5.1 Existing Test Files

| File | Tests | Focus |
|------|-------|-------|
| `tests/unit/test_site_routing.py` | 30+ | Site extraction, filtering, keyword guard, skill compilation, URL encoding |
| `tests/unit/test_verifier.py` | 25+ | All tiers, domain verification, empty verify rejection |
| `tests/integration/test_target_buy_fixes.py` | 15+ | Cross-component: routing+registry, domain injection+verifier, type_text+focus, scroll tiers |
| `tests/unit/test_scroll_action.py` | Unit scroll tests |
| `tests/integration/test_scroll_replan_integration.py` | Scroll + replan integration |

### 5.2 Key Fixtures

- `tmp_log_dir` (conftest.py:302-306): Creates temp directory for EventLogger
- `mock_act`, `mock_coord` (test_verifier.py:33-53): Standard actuator/coordinator mocks
- `SKILL_LIBRARY_DIR` (test_site_routing.py:536-542): Path to real skill library

### 5.3 Test Conventions

- `@pytest.mark.asyncio` for async tests (auto mode in pyproject.toml)
- `@pytest.mark.integration` marker for integration tests
- `@pytest.mark.unit` marker for unit tests
- `tmp_path` fixture for filesystem isolation
- All mocked, no live API calls in unit/integration tests

---

## 6. Constraints

- **Python 3.11** target
- **Line length 100** (Black + Ruff)
- **Ruff rules:** E, W, F, I, B, C4, UP (ignores E501, B008)
- **pytest-asyncio** with `asyncio_mode = "auto"`
- **No inheritance** -- protocols + duck typing
- Every `ActionStep` must have non-empty `verify` (except `done`/`wait_for_user`)
- `.env` leaks `model_provider` -- always pin `model_provider="local"` in test configs
