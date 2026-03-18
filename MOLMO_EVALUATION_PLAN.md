# Molmo Evaluation Plan (Restaurant Automation)

This plan defines a practical A/B benchmark to evaluate whether Molmo improves vision-based desktop automation for the restaurant reservation workflow.

## Goal

Decide if Molmo should replace or augment the current vision model stack for restaurant automation across OpenTable, Yelp, and Google.

## Decision Criteria

Adopt Molmo in production only if all are true after benchmark:

1. End-to-end success rate improves by >= 15 percentage points.
2. First-click accuracy improves by >= 20 percentage points.
3. Median completion time does not regress by > 20%.
4. Manual intervention count does not increase.

## Evaluation Scope

- Domain: Restaurants only
- Providers: OpenTable, Yelp, Google
- Browser: Safari
- Completion boundary: Stop before payment/final irreversible confirmation
- Login behavior: Manual handoff allowed; measure pause/resume correctness

## Variants to Compare

Run all tasks with these variants:

- V0 Baseline: Current vision flow (existing model setup)
- V1 Molmo direct: Replace observer vision calls with Molmo prompts
- V2 Molmo + verification: Molmo + pre-click verification + post-click state check
- Optional V3 Molmo + candidate ranking: Molmo with top-k candidate reasoning

## Test Inputs

Use `MOLMO_EVAL_TASKS.json` for the fixed 20-task benchmark.

Each task includes:
- prompt
- provider
- difficulty
- expected milestone(s)

## Prompt Templates

Use these exact prompt classes for consistency:

1. Observation prompt:
   - "Describe the current page state for restaurant reservation workflow. Include visible restaurant names, filters, date/time controls, and whether a login wall is present."

2. Element grounding prompt:
   - "Find the UI element described as: {target_description}. Return one tight bounding box as normalized coordinates in `<box>(x1,y1,x2,y2)</box>`. Return `<box>NOT_FOUND</box>` if missing."

3. Pre-click verification prompt:
   - "Given the candidate region, what element is this and why does it match `{target_description}`? Answer in one line with confidence 0-1."

4. Post-click validation prompt:
   - "Did the expected transition occur after clicking `{target_description}`? Answer YES/NO and one reason."

## Run Protocol (Per Task)

1. Reset environment:
   - Close extra tabs/windows.
   - Ensure Safari starts from known state.
2. Start timer.
3. Execute automation for chosen variant.
4. Pause for login if needed; resume manually.
5. Stop when:
   - milestone achieved, or
   - max iterations reached, or
   - safety stop triggered.
6. Capture artifacts:
   - initial screenshot
   - final screenshot
   - action log
   - failures/retries
7. Record metrics row in results CSV.

## Metrics

Primary:
- `task_success` (0/1)
- `first_click_correct` (0/1)
- `end_to_end_time_s`
- `actions_total`
- `retries_total`
- `manual_interventions`

Secondary:
- `element_not_found_count`
- `wrong_click_count`
- `login_pause_detected` (0/1)
- `login_resume_success` (0/1)
- `confidence_mean` (if available)

## Scoring

For each variant:

- Success rate = sum(task_success) / N
- First-click accuracy = sum(first_click_correct) / N
- Median completion time = median(end_to_end_time_s where task_success=1)
- Intervention rate = sum(manual_interventions) / N

Also report per-provider breakdown:
- OpenTable success rate
- Yelp success rate
- Google success rate

## Safety Rules

- Never auto-confirm payment.
- Never auto-submit irreversible final booking without explicit user confirmation.
- Abort task after:
  - 2 wrong clicks on critical controls, or
  - 3 consecutive NOT_FOUND on same target, or
  - 1 unexpected navigation to non-allowed domain.

## Allowed Domains

- opentable.com
- yelp.com
- google.com
- maps.google.com

## Logging Schema

Each run should log:

- task_id
- variant
- provider
- prompt
- timestamp_start
- timestamp_end
- status
- failure_reason
- metrics fields from template

Use `MOLMO_EVAL_RESULTS_TEMPLATE.csv` for row format.

## Suggested Execution Order

1. Pilot: run first 5 easy tasks on V0 and V1.
2. Fix prompt/parser issues.
3. Full 20-task run on V0, V1, V2.
4. Compare results against decision criteria.
5. If V2 wins, proceed to integration plan.

## Integration Recommendation If Molmo Wins

1. Keep current planner/orchestrator.
2. Replace only element grounding calls with Molmo adapter.
3. Enable confidence-gated clicking by default.
4. Keep login pause/resume in-loop.
5. Roll out with provider-specific prompt tuning.
