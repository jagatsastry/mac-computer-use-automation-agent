# SOTA Research: Walmart Return Fixes

**Date**: 2026-03-13
**Context**: Customer testing of "return an order on Walmart" revealed 6 bugs (0/3 scenarios passed). This document surveys state-of-the-art approaches to the 4 bug categories identified in `docs/reports/walmart-return/`.

---

## 1. Plan Step Dropping / Truncation Detection

**Our bug**: The LLM generates correct multi-step plans (7 steps, 5 steps), but the orchestrator's plan parser silently drops steps during parsing. Critical "find and click the order" steps vanish between the LLM response and the executed plan. (P0-1)

### SOTA Approaches

#### VerifyLLM — Pre-Execution Plan Verification (Grigorev et al., 2025)
- **Architecture**: Two-agent loop — Planning Agent generates action sequences, Judge LLM critiques them for missing steps, redundancies, and contradictions.
- **Detection method**: Judge produces `(action_index, MISSING|REMOVE, explanation)` tuples. `MISSING` tags describe "what actions are needed" to achieve the stated goal.
- **Performance**: GPT-4-mini achieved 80% recall / 93% precision in single-pass. Iterative refinement (max 3 rounds) boosts recall 5-10%. 96.5% of sequences converge in ≤3 iterations, 62% after round one.
- **Limitation**: Misses context-dependent redundancies and long-range dependencies.
- **Source**: [Plan Verification for LLM-Based Embodied Task Completion Agents](https://arxiv.org/html/2509.02761v2)

#### AgentSpec — Runtime Enforcement DSL (ICSE 2026)
- **Architecture**: Lightweight domain-specific language with `trigger → check → enforce` rules that wrap agent execution.
- **Enforcement options**: `user_inspection`, `llm_self_examine`, `invoke_action`, `stop`.
- **Performance**: Prevents unsafe executions in >90% of cases with millisecond overhead.
- **Limitation**: Reactive (enforces at action time), not proactive (doesn't predict missing steps in advance).
- **Source**: [AgentSpec: Customizable Runtime Enforcement](https://arxiv.org/abs/2503.18666)

#### Resilient Plan-then-Execute Architecture (2025)
- **Key pattern**: Separate planning from execution, validate plan completeness before any action runs.
- **Verification checklist**: Logical coherence, dependency ordering, resource availability, step count matching between LLM output and parsed plan.
- **Recovery**: Distinguish recoverable vs terminal failures; maintain execution state for resumption.
- **Source**: [Architecting Resilient LLM Agents](https://arxiv.org/abs/2509.08646)

### Recommended Pattern for Our Bug

**Pre-execution plan integrity check**: After parsing the LLM's JSON into `ActionStep` objects, compare the parsed step count against the raw JSON step count. If they differ, log a diff and either retry parsing or fail loudly. This is a simple, zero-cost guard that would have caught all 3 scenario failures.

**Concrete implementation**:
1. Extract `len(raw_json["steps"])` from the LLM response before parsing.
2. After `ActionStep.from_dict()` parsing, assert `len(parsed_steps) == expected_count`.
3. If mismatch: log which steps were dropped (by comparing step descriptions), then re-parse with stricter error handling or fall back to the raw JSON steps.

---

## 2. Stale Screen State / Navigation Skips

**Our bug**: When Safari already shows walmart.com/orders from a previous run, the planner omits `open_url` and `activate_app` steps because the screen description shows the page is "already open." The skill template's mandatory navigation steps are treated as optional suggestions. (P0-2)

### SOTA Approaches

#### Playwright — Force Navigation
- Playwright's `page.goto()` always performs a full navigation even if the current URL matches. It reloads the page, ensuring fresh DOM state. The `waitUntil` parameter (`domcontentloaded`, `load`, `networkidle`, `commit`) controls when navigation is considered complete.
- **Key insight**: Idempotent navigation is the default — you never skip `goto()` because "you're already there."
- **Source**: [Playwright Navigations](https://playwright.dev/docs/navigations)

#### Skill Templates as Contracts (Industry Pattern)
- In enterprise RPA tools (UiPath, Automation Anywhere), workflow templates define **mandatory steps** that execute regardless of perceived state. The orchestrator does not optimize away steps based on screen observation.
- **Rationale**: Screen observation is unreliable — a page that "looks right" may be stale, partially loaded, or from a previous session.

#### WebVoyager / SeeAct — Fresh Context Per Task
- Academic web agents (WebVoyager, SeeAct) start each task from a clean browser state or known URL. They do not carry forward state between tasks.
- WebVoyager's action space includes `JUMP/GOOGLE` for explicit navigation, separate from other actions.
- **Source**: [WebVoyager](https://arxiv.org/html/2401.13919v3)

### Recommended Pattern for Our Bug

**Enforce skill-mandated steps as non-negotiable**: When a skill template specifies `open_url` or `activate_app` steps, inject them into the plan unconditionally, regardless of what the planner generates. Two options:

1. **Orchestrator-level injection**: Before executing any plan generated from a skill match, prepend the skill's mandatory navigation steps. The planner's plan is treated as "everything after navigation."
2. **Planner prompt constraint**: Include in the system prompt: "You MUST include all steps from the skill template. Do NOT skip steps because the screen already shows the expected page — always navigate fresh."

Option 1 is more reliable because it doesn't depend on LLM compliance.

---

## 3. Scroll-Based Element Recovery

**Our bug**: When Molmo returns `NOT_FOUND` for an element, the agent declares infeasibility immediately with 0 replans and 0 scroll attempts. The plan's `on_fail: retry_different` and `on_fail: scroll` directives are ignored. (P1-2)

### SOTA Approaches

#### WebVoyager — SCROLL as First-Class Action
- WebVoyager defines 7 actions: `CLICK`, `TYPE`, `SCROLL`, `WAIT`, `BACK`, `JUMP`, `ANSWER`.
- `SCROLL` can target either `WINDOW` (full page) or a specific element by numerical label (for nested scrollable containers).
- **Limitation**: Agents struggle with small scrollable areas and scroll direction confusion. Each error correction consumes one step from the total budget.
- **Source**: [WebVoyager](https://arxiv.org/html/2401.13919v3)

#### Playwright — Auto-Scroll Before Interaction
- Playwright's `scrollIntoViewIfNeeded()` automatically scrolls elements into view before interaction.
- For long pages, nested containers, or sticky headers, `scrollIntoViewIfNeeded()` may not scroll far enough — Playwright scrolls minimally.
- Fallback: `mouse.wheel()` for explicit scroll amounts, `keyboard.press('PageDown')` for page-level scrolling.
- **Source**: [Playwright Scroll to Element](https://www.browserstack.com/guide/playwright-scroll-to-element)

#### Skyvern — Validator-Driven Recovery
- Skyvern's Validator agent checks the outcome of each action. If an action doesn't produce the expected result, it triggers recovery procedures or alternative approaches.
- Architecture: Execution Agent (performs actions) → Validation Agent (confirms outcomes) → Planner (generates alternatives on failure).
- **Source**: [Skyvern GitHub](https://github.com/Skyvern-AI/skyvern)

#### Agent-E — Vision-Based Resilience
- Agent-E (Emergence AI) uses LLMs to reason about what they see rather than relying on fixed selectors. When a button's class name changes, Agent-E recognizes it's still a "Submit" button by visual appearance.
- Achieved 73% on WebVoyager benchmark.
- **Source**: [Agent-E GitHub](https://github.com/EmergenceAI/Agent-E)

### Recommended Pattern for Our Bug

**Scroll-before-fail recovery loop**: When `find_element()` returns `NOT_FOUND`, execute a bounded scroll loop before declaring infeasibility:

```
def find_with_scroll_recovery(element_description, max_scrolls=5):
    for i in range(max_scrolls):
        result = find_element(element_description)
        if result.found:
            return result
        if i < max_scrolls - 1:
            scroll_down(amount=0.5)  # scroll half a viewport
            wait(1.0)  # let page settle / lazy-load
    return NOT_FOUND  # only after exhausting scroll attempts
```

**Key design decisions**:
- **Max scrolls**: 5 (covers ~2.5 full pages, enough for most order lists). Configurable per-step via `on_fail: scroll` metadata.
- **Scroll amount**: Half a viewport per scroll — enough to reveal new content without skipping elements.
- **Wait after scroll**: 1s for lazy-loaded content (Walmart loads orders dynamically).
- **Scroll direction**: Default to down. If the skill step specifies `scroll_up`, honor it. After exhausting downward scrolls with no result, optionally try scrolling back to top and searching again.
- **Screenshot on each scroll**: Capture the viewport at each scroll position for debugging.

---

## 4. Screenshot Capture During Automation

**Our bug**: No screenshots were saved for any of the 3 runs, making post-mortem debugging impossible. We cannot confirm what was actually on screen when grounding failed. (P2-2)

### SOTA Approaches

#### Playwright Trace Viewer
- Records full test execution as a `trace.zip` containing screenshots (film strip), network requests, console logs, and DOM snapshots at every action.
- Configuration: `trace: 'on-first-retry'` (CI) or `trace: 'on'` (always).
- Viewer: `npx playwright show-trace trace.zip` — step-through film strip with action timeline.
- **Source**: [Playwright Trace Viewer](https://playwright.dev/docs/trace-viewer)

#### Selenium Visual Logs
- Visual logs auto-generate screenshots at every Selenium command.
- `driver.save_screenshot('path.png')` for manual capture.
- BrowserStack Automate captures screenshots at every step automatically.
- **Source**: [BrowserStack Screenshot Guide](https://www.browserstack.com/guide/take-screenshot-with-selenium-python)

#### SeleniumBase Trace Mode
- `--trace` flag enables Debug Mode with per-step screenshots.
- `--save-screenshot` captures at end of each test.
- Dashboard mode generates visual reports with embedded screenshots.

### Recommended Pattern for Our Bug

**Per-step screenshot capture in the orchestrator**:

```
def execute_step(step, run_dir):
    # 1. Pre-action screenshot
    pre_path = run_dir / f"step_{step.index:02d}_pre_{step.action}.png"
    screenshot(pre_path)

    # 2. Execute action
    result = actuator.execute(step)

    # 3. Post-action screenshot
    post_path = run_dir / f"step_{step.index:02d}_post_{step.action}.png"
    screenshot(post_path)

    # 4. On failure, also save the screen description
    if not result.success:
        fail_path = run_dir / f"step_{step.index:02d}_fail_{step.action}.png"
        screenshot(fail_path)
        log_screen_description(run_dir, step.index)

    return result
```

**Key design decisions**:
- **Where to save**: `{AGENT_LOG_DIR}/{run_id}/screenshots/` — co-located with `events.jsonl` and `trace.md`.
- **Naming**: `step_{NN}_{pre|post|fail}_{action}.png` — sortable, scannable.
- **Always capture**: Not just on failure. Pre-action shots show what the vision model saw; post-action shots confirm the action's effect.
- **NOT_FOUND captures**: Always save a screenshot when grounding returns NOT_FOUND — this is the single most useful debugging artifact.
- **File size**: At 1024px screenshot width, PNGs are typically 200-500KB. A 15-step plan with pre+post = ~15MB total. Acceptable.
- **Log reference**: Write the screenshot path into `events.jsonl` so the event log links directly to the visual state.

---

## Cross-Cutting Patterns

### Multi-Agent Failure Rates
Recent research (2025) shows 41-86.7% failure rates across 7 SOTA open-source multi-agent systems. Key failure modes:
- **Task verification failures** (wrong-answer, partial completion)
- **Agent-to-agent miscommunication** (dropped context, role confusion)
- **Cascading failures** from early step errors propagating through the pipeline

This validates our experience: 0/3 scenarios passing is within the range of SOTA agent failure rates on complex web tasks. The fixes we're implementing address the most impactful failure modes.

### Plan Quality as a First-Class Metric
SOTA frameworks (DeepEval, AgentIF) now measure:
- **Plan completeness**: Are all necessary steps present?
- **Plan adherence**: Does execution match the plan?
- **Step success rate**: What fraction of planned steps succeed?

Our orchestrator should log these metrics for every run.

---

## Recommendation

**PROCEED** with the fixes as described in the gap report. The patterns identified above are well-established in SOTA frameworks:

| Bug | SOTA Pattern | Complexity | Impact |
|-----|-------------|------------|--------|
| P0-1: Plan step dropping | Pre-execution step count assertion | Low | Blocks all scenarios |
| P0-2: Stale screen navigation | Orchestrator-level mandatory step injection | Low | Blocks 2/3 scenarios |
| P1-2: No scroll recovery | Bounded scroll-before-fail loop | Medium | Blocks all scenarios |
| P2-2: No screenshots | Per-step capture in orchestrator | Low | Debugging only, but critical |

All 4 fixes are standard practices in Playwright, Skyvern, WebVoyager, and enterprise RPA. None require novel research. The hardest fix (scroll recovery) has clear precedent in every SOTA framework examined.
