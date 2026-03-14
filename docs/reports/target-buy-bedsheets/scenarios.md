# Target Buy Bed Sheets — Customer Test Scenarios

**Goal:** "buy the top bed sheet cheaper than $50 on target"
**Date:** 2026-03-13
**Tester:** Customer Agent

## Context

The agent has NO existing "buy on Target" skill. It has:
- A `return-target-order` skill (knows Target.com domain and login flow)
- An `amazon-search` skill (knows how to search and sort by price on Amazon)
- General planning ability to figure out new workflows from scratch

The agent must navigate Target.com, search for bed sheets, apply a price filter (under $50), identify a top-rated option, and add it to cart — all without a pre-built skill.

---

### Scenario 1: Happy Path — Buy Top Bed Sheet Under $50

- **Category:** happy_path
- **Prompt:** "buy the top bed sheet cheaper than $50 on target"
- **Expected outcome:** Agent navigates to Target.com, searches for bed sheets, filters by price (under $50), selects a top-rated/best-selling option, and adds it to cart. Agent should reach the cart or checkout page with the item visible.
- **Success criteria:**
  1. Agent opens Safari and navigates to target.com
  2. Agent searches for "bed sheets" or equivalent search term
  3. Agent applies a price filter (max $50) or sorts by price and selects items under $50
  4. Agent clicks on a bed sheet product that appears to be top-rated or best-selling
  5. Agent clicks "Add to Cart" and the cart confirms the item was added
  6. Agent does NOT proceed past checkout without user confirmation (destructive action gate)
- **Notes:** This is the baseline run. The agent has no Target shopping skill, so it must plan from scratch. Expect it to take multiple steps and possibly some replanning. The agent may use the return-target-order skill's knowledge of Target.com domain structure. Watch for: does the agent use target.com/s?searchTerm= URL pattern, or does it navigate manually through the search bar?

---

### Scenario 2: Skill Adaptation — Same Prompt, Second Run

- **Category:** skill_adaptation
- **Prompt:** "buy the top bed sheet cheaper than $50 on target"
- **Expected outcome:** Agent performs the same task but faster and with fewer retries, because it learned from the first run's execution trace. The skill library should show a new or adapted skill for Target shopping.
- **Success criteria:**
  1. A new skill file exists in the skills library related to Target shopping (or the librarian has promoted observations from Scenario 1)
  2. The agent completes the task in fewer steps or fewer replans than Scenario 1
  3. The agent reuses specific navigation patterns learned from Scenario 1 (e.g., direct URL, known filter locations)
  4. Total execution time is measurably shorter than Scenario 1
- **Notes:** Run this IMMEDIATELY after Scenario 1 completes. The skill librarian should have auto-promoted observations from the first run. If no skill was created, that itself is a test failure — the adaptation system is not working. Compare step counts and replan counts between Scenario 1 and Scenario 2 from the run logs.

---

### Scenario 3: Edge Case — Specific Size and Set

- **Category:** edge_case
- **Prompt:** "buy the cheapest queen-size bed sheet set on Target"
- **Expected outcome:** Agent navigates to Target, searches specifically for queen-size bed sheet sets, sorts or filters by price, and selects the cheapest option. This tests whether any skill adapted from Scenarios 1-2 generalizes to a different but related query.
- **Success criteria:**
  1. Agent searches for "queen size bed sheet set" or similar specific query
  2. Agent finds a way to filter or sort by price (lowest first)
  3. The selected product is specifically a queen-size sheet set (not a flat sheet or pillow case)
  4. Agent adds the cheapest matching item to cart
  5. If a Target shopping skill exists from Scenario 2, the agent matches and uses it (check logs for skill match)
- **Notes:** This tests generalization. The prompt is different enough ("cheapest" vs "top", "queen-size set" vs generic "bed sheet") that a rigid skill would fail, but a well-adapted one should handle it. Also tests whether the agent can handle Target's size selection UI (dropdown or button group for size).

---

### Scenario 4: Error Recovery — Login Required

- **Category:** error_recovery
- **Prompt:** "buy the top-rated bed sheet under $50 on Target and check out"
- **Expected outcome:** Agent navigates to Target, adds an item to cart, and attempts to proceed to checkout. At checkout, Target requires login. The agent should detect the login wall and ask the user to sign in, then continue.
- **Success criteria:**
  1. Agent successfully adds a bed sheet to cart (reusing patterns from earlier scenarios)
  2. When attempting checkout, agent detects the login/sign-in page
  3. Agent pauses and asks user to log in (wait_for_user or equivalent)
  4. Agent does NOT attempt to type credentials or bypass login
  5. If user is already logged in, agent proceeds to checkout and triggers destructive action confirmation before placing order
- **Notes:** This scenario specifically tests error recovery at the checkout boundary. Target requires login to complete purchase. The agent's existing `return-target-order` skill already documents this pattern ("If login page appears: wait for user to log in, then continue"), so the agent should handle this gracefully. The "check out" addition to the prompt pushes the agent further than Scenarios 1-3.

---

### Scenario 5: Edge Case — Out of Stock / Unavailable

- **Category:** edge_case
- **Prompt:** "buy a California King bamboo bed sheet set for under $20 on Target"
- **Expected outcome:** The combination of California King + bamboo + under $20 is extremely restrictive and likely to yield no results or out-of-stock items. The agent should recognize this and report back to the user rather than spinning endlessly.
- **Success criteria:**
  1. Agent searches Target for the specified product
  2. Agent applies price filter or identifies that no items match the criteria
  3. Agent reports to the user that no matching items were found (infeasibility detection)
  4. Agent does NOT add a random non-matching item to cart
  5. Agent completes within a reasonable number of steps (infeasibility detector should trigger before max retries)
- **Notes:** This tests the agent's infeasibility detection. A California King bamboo sheet set under $20 is an unrealistic ask. The agent should hit the frustration score threshold and consult the planner about whether the task is achievable, then report back. If the agent keeps scrolling and retrying endlessly, the infeasibility detection is not working.

---

## Execution Notes

- Run scenarios in order (1 through 5) to test skill adaptation progression
- Between Scenario 1 and 2, check `src/automation_agent/skills/library/` for new skill files
- Compare `logs/runs/<run_id>/events.jsonl` between Scenario 1 and 2 for step count and replan count
- All scenarios should use `--status-ui overlay` flag
- User must be logged into Target.com in Safari before starting Scenario 4
- Warn user before each run so they can step away from the keyboard
