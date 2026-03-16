# Implementation Plan: Direct Reasoning-Model Skill Revision

## Problem Statement

The current adaptive skill pipeline has a fatal structural flaw:

1. **Distiller** produces `SkillObservation` objects with free-text `(category, recommendation)` keys
2. **Experience store** appends them to JSONL keyed by `_normalize_key(category, recommendation)`
3. **Librarian** groups by those normalized keys and requires `min_obs >= 3` from `min_runs >= 2` distinct runs
4. Because the LLM produces varied wording each time, the normalized keys are unique per run

**Evidence**: In 24 live agent runs, 17 observations were collected but zero groups ever reached the promotion threshold. All 9 observations in one block had completely unique wording — 9 groups of 1.

## Proposed Solution

Replace the distiller→experience store→librarian observation-grouping pipeline with a single **SkillReviser** that sends the full execution context to a reasoning model and asks it to produce an improved skill directly.

### Why This Works

- No wording alignment needed — the model sees everything and produces a coherent output
- Single-run capable — no need to accumulate observations across runs
- Full context — the model sees the entire trace, not abstracted fragments

## Architecture

### Data Flow (New)

```
Run completes (with friction: replans, failures, retries)
  → AutomationAgent._maybe_revise_skill()
      → SkillReviser.revise()
          → Build prompt: original skill MD + execution trace + screen descriptions
             + replan patches + derived session + original plan + revision history
          → call_skill_llm(prompt, model=reasoning_model, max_tokens=8192)
          → Parse response: revised skill MD + change_summary + confidence
          → SkillRevisionResult
      → If confidence >= threshold (0.6):
          → Validate revised skill via parse_skill_file()
          → Structural check: name/keywords/params unchanged
          → _atomic_write() to disk
          → load_from_string() into registry
          → Write revision history to JSONL
```

### Trigger Condition

Revise when ANY of:
- `had_replan == True`
- Any `StepResult` has `success == False`
- Any `StepResult` has non-empty `retry_strategies_used`
- Run completed but `success == False`

Do NOT revise on clean runs with zero friction.

## New/Modified Files

### New Files

| File | Purpose |
|------|---------|
| `src/automation_agent/skills/reviser.py` | Core `SkillReviser` class |
| `src/automation_agent/skills/prompts/revise_skill.md` | Reasoning model prompt template |
| `tests/unit/test_skill_reviser.py` | Unit tests |

### Modified Files

| File | Change |
|------|--------|
| `src/automation_agent/skills/models.py` | Add `SkillRevisionResult` dataclass |
| `src/automation_agent/skills/registry.py` | Replace `learn_from_run`/`promote_from_run` with `revise_from_run` |
| `src/automation_agent/orchestrator/agent.py` | Replace `_maybe_learn_skill_run` + `_maybe_promote_skill` with `_maybe_revise_skill` |
| `src/automation_agent/config.py` | Add `skill_revision_*` config fields |

### Removed (Phase 3)

| File | Reason |
|------|--------|
| `src/automation_agent/skills/distiller.py` | Replaced by reviser |
| `src/automation_agent/skills/experience.py` | No longer needed |
| `src/automation_agent/skills/librarian.py` | Commit utils extracted; rest replaced |

## SkillReviser Class Design

```python
class SkillReviser:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._prompt_template = _load_template("revise_skill.md")

    async def revise(
        self,
        skill: Skill,
        goal: str,
        trace: List[StepResult],
        *,
        original_plan_steps: str = "",
        derived_session: Optional[DerivedSkillSession] = None,
        replan_patches: List[ReplanPatch] = None,
        run_id: str = "",
        success: bool = True,
    ) -> Optional[SkillRevisionResult]:
        """Produce a revised skill from a single run's execution context."""
        if not self._has_friction(trace, success):
            return None

        prompt = self._build_prompt(skill, goal, trace, ...)
        raw = await call_skill_llm(self.config, prompt, max_tokens=8192, temperature=0.0)
        result = self._parse_response(raw, skill.name, run_id)

        if result is None or result.revised_md is None:
            return result
        if result.confidence < self.config.skill_revision_min_confidence:
            return result  # logged but not applied
        if not self._validate_revision(skill, result.revised_md):
            return None

        return result
```

## Prompt Template Design

```markdown
You are a skill improvement engine for a macOS desktop automation agent.

Given a reusable skill template and a complete execution trace where the skill
encountered difficulties, produce an improved version that avoids those difficulties.

## Rules
- Preserve name, skill-id, trigger-keywords, parameters, requires, site EXACTLY
- Only modify sections where trace evidence supports the change
- Keep all steps that succeeded verbatim
- Add new steps/tips rather than removing existing ones
- If a step failed because an element was absent, add Error Recovery for that scenario
- Every step must have a non-empty verify field
- If nothing to improve, return {"revised_skill_md": null, "confidence": 0.0, ...}

## Original Skill
{{original_skill_md}}

## Original Plan
{{original_plan}}

## Execution Trace
{{execution_trace}}

## Replan Patches Applied
{{replan_patches}}

## Derived Session
{{derived_session}}

## Run Metadata
Goal: {{goal}} | Run: {{run_id}} | Outcome: {{outcome}} | Replans: {{replan_count}}

## Previous Revisions (last 3)
{{revision_history}}

## Output
Return ONLY valid JSON:
{
  "revised_skill_md": "---\nname: ...\n---\n...",
  "change_summary": "One-line description",
  "confidence": 0.0-1.0,
  "changes": [{"section": "...", "type": "...", "description": "..."}]
}
```

## SkillRevisionResult Dataclass

```python
@dataclass
class SkillRevisionResult:
    skill_name: str
    original_md: str
    revised_md: Optional[str]  # None if no revision needed
    change_summary: str
    confidence: float
    changes: List[Dict[str, str]]
    run_id: str
    timestamp: str  # ISO 8601
    applied: bool = False
    rollback_reason: str = ""
```

## Config Changes

**New:**
- `skill_revision_enabled: bool = True`
- `skill_revision_model: str = ""` (defaults to text_model)
- `skill_revision_min_confidence: float = 0.6`
- `skill_revision_max_history: int = 3`

**Deprecated (Phase 3):**
- `skill_librarian_enabled`, `skill_librarian_min_*`, `skill_librarian_max_tips`

**Kept:**
- `skill_learning_enabled` (gates entire subsystem)
- `skill_learning_dir` (stores revision history)

## Safety / Regression Prevention

1. **Structural validation**: Revised skill must parse via `parse_skill_file()`. Name, skill-id, trigger-keywords, parameters, site must match original exactly.

2. **Revision history with rollback**: Each revision logged to `logs/skill_learning/revisions/<skill_name>.jsonl` with original content, revised content, change summary, run_id, timestamp. Enables rollback.

3. **Conservative prompt**: Model instructed to only modify what trace evidence supports, add rather than remove, keep successful steps verbatim.

4. **Anti-oscillation**: Last 3 revisions included in prompt so model can see its own history and avoid flip-flopping.

## Implementation Phases

### Phase 1: Core Reviser (testable independently)
1. Add `SkillRevisionResult` to `models.py`
2. Create `revise_skill.md` prompt template
3. Create `reviser.py`
4. Extract commit utilities from `librarian.py`
5. Unit tests

### Phase 2: Integration
6. Config fields
7. Modify `registry.py`: `revise_from_run()`
8. Modify `agent.py`: `_maybe_revise_skill()`
9. Update integration tests

### Phase 3: Cleanup
10. Remove distiller, experience store, old prompts
11. Remove librarian
12. Remove deprecated config fields

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Invalid skill MD from LLM | `parse_skill_file()` validation + structural checks |
| Model changes routing metadata | Hard reject if name/keywords/params differ |
| Model removes working steps | Prompt: "only modify what trace supports" |
| Oscillation between revisions | Include last 3 revisions in prompt |
| Token budget for large traces | Cap trace to last 20 steps; Gemini has 1M context |
| Race condition on concurrent runs | `_atomic_write()` is already atomic |
| LLM cost | Replaces 2 LLM calls (distiller + librarian decide) with 1. Net cheaper. |
