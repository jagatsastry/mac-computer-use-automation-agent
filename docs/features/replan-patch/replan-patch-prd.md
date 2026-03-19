# Replan as Plan Patch + Materialized Plan Files

## Problem
When a step fails verification, the agent replans by generating a completely new plan from scratch. This loses context — the LLM doesn't know which steps succeeded, what the original plan looked like, or what the current position is. This leads to nonsensical replans like `activate_app('orders page')`.

## Solution
Replan is now a **plan patch** — it keeps completed steps, replaces remaining steps from the current position forward. The replan prompt shows the original plan with pass/fail annotations, the current screen (as a screenshot), and asks "what should the plan look like from here on?"

Additionally, every plan and replan is **materialized to a timestamped file** in the run directory for debugging.

## User-Visible Changes

### 1. Smarter replanning
When a step fails, the LLM now sees:
```
Step 0: open_url(url='https://amazon.com/orders') check — Browser URL matches
Step 1: type_text(text='listerine', element='Search input') cross — Vision denies: field empty
Step 2: click(element='Return button') — (not attempted)
```
Instead of a flat "Step 0: failed" history. This means:
- The replan knows which steps already succeeded (won't redo them)
- The replan knows exactly what failed and why
- If text was typed but verify failed because results haven't appeared yet, the replan should add `press_key(["return"])` instead of retyping

### 2. Plan files saved to disk
Every plan and replan is saved to `logs/runs/{run_id}/plans/`:
```
plan_v0_20260318T114309.json    # initial plan
plan_v1_20260318T114522.json    # first replan
plan_v2_20260318T114730.json    # second replan
```

Replan files include:
- `trigger`: what failed to cause the replan
- `resume_from_step`: which step the new plan starts from
- `completed_steps`: summary of steps that already passed
- `steps`: the new/replacement steps

### 3. Screenshot sent to planner (for cloud providers)
For Gemini and Anthropic planning providers, the replan now sends a screenshot directly to the LLM instead of a slow text description. This saves 6-20 seconds per replan and gives the LLM a more accurate view of the current screen state.

## How to test
- Run any multi-step task that's likely to have a step fail (e.g., searching for a specific product)
- Check `logs/runs/{run_id}/plans/` for materialized plan files
- If a replan occurs, verify the plan_v1 file shows `completed_steps` and `resume_from_step`
- The replan should generate new steps starting from the failed position, not a full new plan
