# Replan-Patch Customer Test Scenarios

These scenarios are designed to exercise the replan-patch feature by triggering replans during multi-step desktop automation tasks. Each scenario targets a situation where at least one step is likely to fail verification, forcing the agent to replan using the new plan-patch approach.

---

### Scenario 1: Search Wikipedia for a specific article
- **Category:** happy_path
- **Prompt:** "Open Safari, go to wikipedia.org, and search for 'Voyager 1 golden record'"
- **Expected outcome:** The Wikipedia article for the Voyager Golden Record is displayed in the browser.
- **Success criteria:**
  1. A `plan_v0_*.json` file exists in `logs/runs/{run_id}/plans/`
  2. The initial plan contains at least 3 steps (open browser, navigate, search)
  3. If a replan occurs, a `plan_v1_*.json` file exists with `resume_from_step` and `completed_steps` fields
  4. The replan file's `completed_steps` does not re-include steps that already passed (e.g., opening the URL)
  5. The Wikipedia search results or article page is visible on screen at the end
- **Notes:** Search boxes on Wikipedia can be tricky to locate and type into. The verify step after type_text may fail if the search field is not focused or the text did not land, triggering a replan. The replan should patch from the failed step forward rather than starting over from "open Safari."

---

### Scenario 2: Use Calculator to compute a multi-step expression
- **Category:** happy_path
- **Prompt:** "Open Calculator and compute 156 times 23"
- **Expected outcome:** Calculator app is open and displays the result 3588.
- **Success criteria:**
  1. A `plan_v0_*.json` file exists in `logs/runs/{run_id}/plans/`
  2. If a type or click step fails verification (e.g., wrong button clicked, display not updated), a replan file `plan_v1_*.json` is created
  3. Any replan file includes `trigger` describing what failed
  4. The Calculator app is in the foreground at the end with a numeric result displayed
  5. The run's `report.md` mentions the replan if one occurred
- **Notes:** Calculator button grounding can fail because the buttons are small and close together. If the agent clicks the wrong button or the display does not show the expected intermediate value, verification fails and a replan is needed. The replan should know which digits were already entered successfully.

---

### Scenario 3: Search for a nonexistent product on Amazon
- **Category:** edge_case
- **Prompt:** "Go to amazon.com and search for 'zxqwv7 phantom gadget 9000'"
- **Expected outcome:** Amazon search results page is shown (likely with "no results" or unrelated suggestions).
- **Success criteria:**
  1. A `plan_v0_*.json` file exists in `logs/runs/{run_id}/plans/`
  2. At least one replan file (`plan_v1_*.json`) exists because the search or its verification is likely to struggle
  3. Each replan file contains `completed_steps` showing which earlier steps passed
  4. No replan file repeats an already-completed step like `open_url` in its new steps
  5. The final screen shows Amazon with the search term in the search bar or in the results heading
- **Notes:** This intentionally uses a gibberish search term. The agent will open Amazon, find the search box, and type the text. Verification of "search results appeared" is likely to fail or be ambiguous since there are no real results for this term. This may trigger multiple replans as the agent tries pressing Enter, re-typing, or re-verifying. The key test is that each successive replan patches forward rather than regenerating from scratch.

---

### Scenario 4: Navigate to a page via a misremembered URL
- **Category:** error_recovery
- **Prompt:** "Open Safari and go to news.ycombinator.com, then click on the second story link"
- **Expected outcome:** A Hacker News story page is open in the browser.
- **Success criteria:**
  1. A `plan_v0_*.json` file exists with the initial plan
  2. If clicking "the second story link" fails verification (wrong element, page not navigated), a `plan_v1_*.json` replan file is created
  3. The replan's `trigger` field describes the click failure (e.g., "Vision denies: page unchanged" or similar)
  4. The replan's `completed_steps` correctly marks the URL navigation step as passed
  5. The final state shows a Hacker News story page (not the front page) loaded in the browser
- **Notes:** Clicking "the second story link" on Hacker News requires precise element grounding on a dense text-heavy page. The agent is likely to mis-click on an adjacent link, a comment count, or a domain label instead of the actual story title. When verification detects the wrong page loaded (or no navigation happened), the replan should keep the already-completed navigation to news.ycombinator.com and only retry the click step.
