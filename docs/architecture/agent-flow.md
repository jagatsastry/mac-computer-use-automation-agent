# Automation Agent Flow

## Model Assignment

```
┌─────────────────────────────────────────────────────┐
│                   Claude Sonnet 4                    │
│              (Anthropic API / remote)                │
│                                                     │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Planning │  │ Describe     │  │ Verify        │  │
│  │          │  │ Screen       │  │ Condition     │  │
│  │ text in  │  │ screenshot   │  │ screenshot +  │  │
│  │ JSON out │  │ → text desc  │  │ question      │  │
│  │          │  │              │  │ → YES/NO      │  │
│  └──────────┘  └──────────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│                  Molmo v1 (MLX)                      │
│             (localhost:8091 / local)                  │
│                                                     │
│  ┌──────────────────────────────────────────────┐    │
│  │ Grounding (find_element)                     │    │
│  │ screenshot + "Find the Search button"        │    │
│  │ → x, y coordinates + confidence              │    │
│  └──────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

## Execution Flow

```
User prompt: "Return my listerine on amazon"
│
▼
┌──────────────────────────────────────────────────────────┐
│ 1. SKILL MATCHING                                        │
│    Registry.match(prompt)                                │
│    ├─ keyword scan → "return", "amazon" → return-amazon  │
│    └─ match type: direct | analogical | generic          │
│                                                          │
│    Output: skill context (steps, verify conditions)      │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ 2. SCREEN DESCRIPTION                          [Claude]  │
│    coordinator.describe_screen()                         │
│    ├─ capture screenshot                                 │
│    ├─ send screenshot + prompt to Claude                 │
│    └─ returns: "Desktop visible, Safari in Dock..."      │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ 3. PLANNING                                    [Claude]  │
│    planner.plan(goal, screen_desc, skill_context)        │
│    ├─ fills plan_from_prompt.md template                 │
│    ├─ sends to Claude API                                │
│    └─ parses JSON → ActionPlan(steps=[...])              │
│                                                          │
│    Output: [{action, params, verify, on_fail}, ...]      │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ 4. PLAN VALIDATION                                       │
│    plan.validate()                                       │
│    ├─ every step has non-empty "verify" (except done)    │
│    ├─ every step has "on_fail"                           │
│    └─ reject if validation fails                         │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────┐
                    │  FOR EACH STEP IN PLAN   │◄────────────────────┐
                    └────────────┬─────────────┘                     │
                                 │                                   │
                                 ▼                                   │
               ┌─────────────────────────────────┐                   │
               │ step.action == "click" with      │                   │
               │ element description?             │                   │
               └──────┬──────────────┬────────────┘                   │
                  yes │              │ no                              │
                      ▼              │                                │
┌──────────────────────────────┐     │                                │
│ 5a. GROUNDING        [Molmo] │     │                                │
│ coordinator.find_element()   │     │                                │
│ ├─ capture screenshot        │     │                                │
│ ├─ send to Molmo:            │     │                                │
│ │   "Find: Search button"    │     │                                │
│ ├─ parse → (x, y, conf)     │     │                                │
│ ├─ confidence gate:          │     │                                │
│ │   normal ≥ 0.5             │     │                                │
│ │   critical ≥ 0.9           │     │                                │
│ │   (submit/pay/delete)      │     │                                │
│ └─ optional: validate with   │     │                                │
│    200×200 crop → Claude     │     │                                │
└──────────────┬───────────────┘     │                                │
               │                     │                                │
               ▼                     ▼                                │
┌──────────────────────────────────────────────────────────┐          │
│ 5b. EXECUTE ACTION                                       │          │
│     actuator.execute(action, params)                     │          │
│     ├─ click(x, y)      ├─ type_text(text)              │          │
│     ├─ open_url(url)     ├─ press_key(keys)             │          │
│     ├─ activate_app(app) ├─ scroll(dir, amount)         │          │
│     └─ quit_app(app)                                     │          │
│                                                          │          │
│     Actuator chain: Bridge HTTP → hs CLI → AppleScript   │          │
└──────────────────────────────────┬───────────────────────┘          │
                                   │                                  │
                                   ▼                                  │
┌──────────────────────────────────────────────────────────┐          │
│ 5c. SCREENSHOT DIFF (click/open_url only)                │          │
│     ├─ compare before/after screenshots                  │          │
│     ├─ if no visible change → mark action as failed      │          │
│     └─ region_changed(click_x, click_y) check            │          │
└──────────────────────────────────┬───────────────────────┘          │
                                   │                                  │
                                   ▼                                  │
┌──────────────────────────────────────────────────────────┐          │
│ 6. THREE-TIER VERIFICATION                               │          │
│                                                          │          │
│  Tier 0: Accessibility API                    (~10ms)    │          │
│  ├─ check focused element, window title, app state       │          │
│  ├─ text field role detection for type_text              │          │
│  └─ if conclusive → done                                 │          │
│          │                                               │          │
│          ▼ inconclusive                                  │          │
│  Tier 1: Actuator State Query                 (~50ms)    │          │
│  ├─ activate_app → check frontmost app name              │          │
│  ├─ open_url → check browser URL                         │          │
│  ├─ type_text → check focused field content              │          │
│  ├─ click/press_key/scroll → always inconclusive         │          │
│  └─ if conclusive → done                                 │          │
│          │                                               │          │
│          ▼ inconclusive                                  │          │
│  Tier 2: Vision Verification              [Claude] (2-5s)│          │
│  ├─ capture screenshot                                   │          │
│  ├─ send to Claude: "Is this true? [verify condition]"   │          │
│  └─ parse response: YES → pass, NO → fail, UNCLEAR → ?  │          │
└──────────────────────────────────┬───────────────────────┘          │
                                   │                                  │
                          ┌────────┴────────┐                         │
                          │                 │                         │
                     verified           failed                        │
                          │                 │                         │
                          ▼                 ▼                         │
                    ┌──────────┐   ┌──────────────────┐               │
                    │ next step│   │ ON_FAIL HANDLER   │               │
                    │    ──────┼──►│                    │               │
                    └──────────┘   │ retry_different:   │               │
                                   │   re-execute step  ├──────────────┘
                                   │                    │
                                   │ replan:            │
                                   │   call planner     ├──► back to step 3
                                   │   with history     │
                                   │                    │
                                   │ abort:             │
                                   │   stop execution   ├──► ExecutionResult
                                   │                    │       (failure)
                                   │ wait_for_user:     │
                                   │   pause for input  │
                                   └────────────────────┘

                          │
                          ▼ (all steps done)
┌──────────────────────────────────────────────────────────┐
│ 7. SKILL LEARNING (optional)                             │
│    ├─ distiller extracts observations from execution     │
│    ├─ experience store appends to JSONL sidecar          │
│    └─ librarian evaluates promotion (patch/sibling/etc)  │
└──────────────────────────────────┬───────────────────────┘
                                   │
                                   ▼
                          ExecutionResult
                          (success/failure + step details)
```

## Data Flow Per Step

```
                    ┌─────────────┐
                    │  ActionStep │
                    │  action     │
                    │  params     │
                    │  verify     │
                    │  on_fail    │
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
   ┌────────────┐  ┌────────────┐  ┌──────────────┐
   │ find_element│  │  execute   │  │   verify     │
   │   [Molmo]   │  │ [Actuator] │  │  [Claude]    │
   │             │  │            │  │              │
   │ screenshot  │  │ click(x,y) │  │ screenshot + │
   │ + "Find X"  │  │ type(text) │  │ "Is X true?" │
   │ → (x,y,conf)│  │ open(url)  │  │ → YES/NO     │
   └────────────┘  └────────────┘  └──────────────┘
```

## Cost Estimate Per Step

| Operation | Model | Latency | Cost |
|-----------|-------|---------|------|
| Screen describe | Claude | ~3s | ~$0.005 |
| Planning | Claude | ~3-5s | ~$0.01 |
| Find element | Molmo (local) | ~8-10s | $0 |
| Verify condition | Claude | ~2-3s | ~$0.003 |
| **Per step total** | | ~15-20s | ~$0.008 |
| **Typical 5-step task** | | ~1-2 min | ~$0.05 |
