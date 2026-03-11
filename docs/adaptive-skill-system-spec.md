# Adaptive Skill System: Architecture Specification

**Status**: Draft — pending DE review and Short-Seller attack
**PRD**: `docs/adaptive-skill-system-prd.md`
**SOTA**: `docs/adaptive-skill-system-sota.md`
**Codebase Analysis**: `docs/adaptive-skill-system-codebase.md`
**Original Spec**: `docs/adaptive-skill-system.md`

---

## 1. System Design Overview

### 1.1 Problem Summary

The current skill system returns one skill per prompt, has no analogical matching, and loses corrections between replans. This spec adds:

1. **Top-k routing** with direct/analogical/generic labels
2. **Multi-skill planner context** so the LLM sees multiple priors
3. **Run-local derived procedure** that captures corrections during execution
4. **Stateful replanning** that updates the derived procedure and feeds it forward

### 1.2 SOTA Approach Chosen

**LLM-heavy routing** (RankRAG pattern): Send all compact skill cards to one LLM call, ask for top-3 with match_type labels. No embedding infrastructure.

**ReCAP-style correction flow**: Corrections from the single replan are captured in structured form via `ReplanPatch` and stored on the `DerivedSkillSession`. In the current single-replan architecture (constraint 6), the derived procedure provides two concrete benefits: (1) the planner sees the accumulated corrections in `skill_context` during the replan call itself (richer context than raw step_results alone), and (2) the post-run distiller receives structured correction data (failed assumptions, successful adaptations, label replacements) for better skill learning. The architecture is designed so that if multi-replan is added later, corrections flow forward automatically — but in MVP, the single replan is the only consumer of the seeded procedure, and the distiller is the only consumer of the updated procedure.

**Reflexion separation**: Within-run tactical correction (derived procedure) is separate from post-run strategic learning (distiller observations). Note: The ExpeL success/failure pair comparison pattern (comparing successful vs. failed trajectory segments for richer insight extraction) is **not implemented in MVP**. The derived procedure's `failed_assumptions` and `successful_adaptations` fields provide a lightweight approximation — they capture what failed and what worked within a single run, but the distiller does not yet compare them as structured pairs. Full ExpeL-style pair comparison is a post-MVP enhancement to the distiller prompt.

### 1.3 Data Flow

```
User prompt
  |
  v
SkillRegistryImpl.match(prompt)
  |
  +-> SkillCardBuilder.build_all() -> List[SkillCard]
  +-> SkillRouter.route(prompt, cards) -> SkillRouteResult
  |     (top-k candidates with match_type, confidence, reason)
  |
  +-> Build multi-skill context string
  |
  v
Return SkillRouteResult to orchestrator
  |
  v
AutomationAgent.execute()
  |
  +-> Create DerivedSkillSession (seeded from best parent skill)
  +-> Assemble skill_context string (multi-skill + derived procedure)
  +-> planner.plan(goal, ..., skill_context=assembled_string)
  |
  +-> Execute steps -> verify -> on failure:
  |     +-> planner.replan(..., skill_context=assembled_string_with_derived)
  |     +-> Parse ReplanPatch from response (optional, best-effort)
  |     +-> DerivedSkillSession.apply_patch(patch)
  |     +-> Continue execution with updated context
  |
  +-> Post-run: _maybe_learn_skill_run() with derived procedure in context
  v
Done
```

### 1.4 Component Map

| Component | File(s) | What Changes |
|-----------|---------|-------------|
| **SkillCard** | `skills/models.py` | New dataclass |
| **SkillCardBuilder** | `skills/card_builder.py` | New file |
| **SkillRouteResult** | `shared_models.py` | New dataclass |
| **SkillMatchResult** | `shared_models.py` | New dataclass (replaces Dict from match()) |
| **DerivedSkillSession** | `skills/derived_skill.py` | New file |
| **ReplanPatch** | `shared_models.py` | New dataclass (same file as ActionPlan) |
| **SkillRouter** | `skills/router.py` | Updated: top-k output, card-based summary |
| **Router prompt** | `skills/prompts/route_skill.md` | Updated: request top-3 + match_type |
| **SkillRegistryImpl** | `skills/registry.py` | Updated: match() returns new shape, multi-skill context, cached cards |
| **Skill** | `skills/models.py` | Updated: optional `skill_id`, `tags`, `summary` fields |
| **Skill loader** | `skills/loader.py` | Updated: parse new optional frontmatter fields |
| **ActionPlannerImpl** | `planner/planner.py` | Updated: parse optional `derived_skill_patch` from replan |
| **Plan prompt** | `planner/prompts/plan_from_prompt.md` | Updated: multi-skill priors section |
| **Replan prompt** | `planner/prompts/replan_from_state.md` | Updated: derived procedure + patch request |
| **AutomationAgent** | `orchestrator/agent.py` | Updated: DerivedSkillSession lifecycle, context assembly |
| **Skill library** | `skills/library/*.md` | Updated: add optional `skill-id`, `tags`, `summary` |

**Type placement rationale**: Types are placed by their dependency scope:
- **`shared_models.py`**: Types that cross package boundaries or appear in protocol signatures — `MatchType`, `SkillRouteCandidate`, `SkillRouteResult`, `SkillMatchResult`, `ReplanPatch`. These are imported by `planner/`, `orchestrator/`, and `skills/`.
- **`skills/models.py`**: Types internal to the skills package — `SkillCard`, `Skill`. Only consumed by `skills/card_builder.py`, `skills/router.py`, `skills/registry.py`. Never appear in protocol signatures.
- **`skills/derived_skill.py`**: `DerivedSkillSession` — used by `orchestrator/agent.py` and `skills/` but has zero imports from either (pure stdlib types). Placed in `skills/` because it is conceptually a skill-domain object.

This split avoids circular imports and keeps `shared_models.py` lean (only protocol-boundary types).

### 1.5 What Does NOT Change

- `protocols.py` method signatures stay identical EXCEPT `SkillRegistry.match()` return type changes from `Optional[Dict[str, Any]]` to `Optional[SkillMatchResult]` (dict-compat shims preserve existing call sites)
- `shared_models.py` existing dataclasses (`ActionStep`, `StepResult`, `ExecutionResult`, `FindElementResult`) — `ActionPlan` gains one optional field (`replan_patch`)
- `StepVerifier` and `GroundingRouter`
- `SkillDistiller` interface (receives derived procedure via `skill_context` string)
- `SkillExperienceStore`
- `matcher.py` keyword fallback logic is unchanged EXCEPT `match_skill()` return type gains a third element (keyword hit count `int`) so the registry can compute proportional confidence
- `AutomationAgent.__init__` constructor signature and instance state (derived session is a local variable in `execute()`, not instance state)

---

## 2. Exact Data Shapes

### 2.1 SkillCard

**File**: `src/automation_agent/skills/models.py`

```python
@dataclass
class SkillCard:
    """Compact routing card for LLM-based skill selection.

    Generated by SkillCardBuilder and cached on SkillRegistryImpl at load time.
    Rebuilt when skills change (load_from_directory / load_from_string).
    Not persisted to disk — lives in memory alongside the Skill objects.
    """

    skill_id: str          # e.g. "return-amazon-order"
    title: str             # e.g. "Amazon Return"
    summary: str           # Abstraction-first: "Navigate a retailer's order history..."
    tags: list[str]        # e.g. ["ecommerce", "return", "refund"]
    required_apps: list[str]  # From Skill.requires.apps — deterministic pre-filter
    required_os: str       # From Skill.requires.os — deterministic pre-filter
```

**Validation rules**:
- `skill_id` must be non-empty. Defaults to `Skill.name` if no `skill-id` in frontmatter.
- `summary` must be non-empty. If no `summary` in frontmatter, falls back to `Skill.description`.
- `tags` may be empty.
- `title` defaults to titlecased `skill_id` with hyphens replaced by spaces.

**Example instance**:
```python
SkillCard(
    skill_id="return-amazon-order",
    title="Return Amazon Order",
    summary="Navigate a retailer's order history, locate a purchased item, and complete a return or refund flow; currently specialized for Amazon.",
    tags=["ecommerce", "return", "refund", "amazon"],
    required_apps=[],
    required_os="darwin",
)
```

### 2.2 Updated Skill Dataclass

**File**: `src/automation_agent/skills/models.py`

Add three optional fields to `Skill`:

```python
@dataclass
class Skill:
    # ... existing fields unchanged ...
    skill_id: str = ""          # Optional: from frontmatter `skill-id`
    tags: list[str] = field(default_factory=list)  # Optional: from frontmatter `tags`
    summary: str = ""           # Optional: from frontmatter `summary`
```

**Backward compatibility**: All three default to empty/empty-list. Existing skills without these fields load identically to today.

### 2.3 SkillRouteResult

**File**: `src/automation_agent/shared_models.py`

```python
from enum import StrEnum


class MatchType(StrEnum):
    """How a skill relates to the user prompt."""

    DIRECT = "direct"          # Skill designed for exactly this task
    ANALOGICAL = "analogical"  # Structurally similar, adapt labels/nav
    GENERIC = "generic"        # General utility (e.g., app navigation)


@dataclass
class SkillRouteCandidate:
    """A single candidate skill from the router."""

    skill_id: str
    match_type: MatchType    # StrEnum: direct | analogical | generic
    confidence: float        # 0.0 to 1.0
    reason: str              # Router's rationale

    def __post_init__(self) -> None:
        if isinstance(self.match_type, str):
            self.match_type = MatchType(self.match_type)
        self.confidence = max(0.0, min(1.0, self.confidence))


@dataclass
class SkillRouteResult:
    """Result of top-k skill routing."""

    candidates: list[SkillRouteCandidate]

    @property
    def primary(self) -> Optional[SkillRouteCandidate]:
        """Highest-confidence candidate, or None if empty."""
        return self.candidates[0] if self.candidates else None

    @property
    def has_direct_match(self) -> bool:
        return any(c.match_type == MatchType.DIRECT for c in self.candidates)
```

**Why `MatchType.GENERIC` instead of `FALLBACK`**: The word "fallback" is already used in the codebase for the keyword matcher code path (`match_skill()` in `matcher.py` is the "keyword fallback"). Using "generic" for the match type avoids overloading the term. "Generic" means "this skill provides general utility" — e.g., `open-app-and-navigate` is generic for any browser task.

**Note**: `SkillRouteResult` is used internally. The `SkillRegistryImpl.match()` method still returns `Optional[Dict[str, Any]]` (protocol-compatible) — it converts the result into a dict with the shape described in section 2.7.

### 2.4 DerivedSkillSession

**File**: `src/automation_agent/skills/derived_skill.py` (new)

```python
@dataclass
class DerivedSkillSession:
    """Run-local derived procedure. In-memory only, discarded after run.

    Created when a skill match occurs. Seeded from the best parent skill.
    Updated by ReplanPatch during replanning.
    """

    parent_skill_ids: list[str]      # Skill IDs that seeded this session
    match_types: list[MatchType]     # Corresponding match types (StrEnum values)
    current_steps: str               # Best-known step sequence (starts as parent steps)
    replaced_labels: list[dict]      # [{"old": str, "new": str, "reason": str}]
    discovered_landmarks: list[str]  # UI elements/text discovered during run
    verification_notes: list[str]    # Improved verify conditions
    failed_assumptions: list[str]    # What didn't work
    successful_adaptations: list[str]  # What worked differently from parent

    _MAX_ITEMS = 20  # Cap per list to bound context size

    def apply_patch(self, patch: "ReplanPatch") -> None:
        """Apply a replan patch. Deduplicates and caps all lists.

        - replaced_labels: dedup by 'old' key (last write wins)
        - string lists: set semantics (no duplicates)
        - all lists capped at _MAX_ITEMS (oldest dropped)
        - idempotent: applying the same patch twice is a no-op
        """
        # Labels: dedup by 'old' key — newer replacement wins
        existing_olds = {r["old"] for r in self.replaced_labels}
        for label in patch.replace_labels:
            if label["old"] in existing_olds:
                # Replace existing entry
                self.replaced_labels = [
                    r for r in self.replaced_labels if r["old"] != label["old"]
                ]
            self.replaced_labels.append(label)
        self.replaced_labels = self.replaced_labels[-self._MAX_ITEMS:]

        # String lists: set semantics + cap
        self.discovered_landmarks = self._merge_capped(
            self.discovered_landmarks, patch.add_landmarks
        )
        self.verification_notes = self._merge_capped(
            self.verification_notes, patch.verify_improvements
        )
        self.failed_assumptions = self._merge_capped(
            self.failed_assumptions, patch.failed_assumptions
        )
        self.successful_adaptations = self._merge_capped(
            self.successful_adaptations, patch.successful_adaptations
        )

        if patch.revised_steps:
            self.current_steps = patch.revised_steps

    @staticmethod
    def _merge_capped(existing: list[str], new: list[str], cap: int = 20) -> list[str]:
        """Merge new items into existing with set semantics and cap."""
        seen = set(existing)
        merged = list(existing)
        for item in new:
            if item not in seen:
                merged.append(item)
                seen.add(item)
        return merged[-cap:]

    def serialize_for_context(self) -> str:
        """Serialize to a string for injection into skill_context."""
        sections = ["## Derived Procedure (run-local, current best hypothesis)"]
        sections.append(f"Parent skill(s): {', '.join(self.parent_skill_ids)}")
        if self.replaced_labels:
            sections.append("### Label Replacements")
            for r in self.replaced_labels:
                sections.append(
                    f"- \"{r['old']}\" -> \"{r['new']}\" ({r.get('reason', '')})"
                )
        if self.discovered_landmarks:
            sections.append("### Discovered Landmarks")
            for lm in self.discovered_landmarks:
                sections.append(f"- {lm}")
        if self.verification_notes:
            sections.append("### Verification Notes")
            for v in self.verification_notes:
                sections.append(f"- {v}")
        if self.failed_assumptions:
            sections.append("### Failed Assumptions (do NOT repeat)")
            for f in self.failed_assumptions:
                sections.append(f"- {f}")
        if self.successful_adaptations:
            sections.append("### Successful Adaptations")
            for s in self.successful_adaptations:
                sections.append(f"- {s}")
        return "\n".join(sections)

    @classmethod
    def seed(
        cls,
        parent_skill_ids: list[str],
        match_types: list[str],
        steps_text: str,
    ) -> "DerivedSkillSession":
        """Create a session seeded from routing candidates.

        Args:
            parent_skill_ids: Skill IDs of matched candidates.
            match_types: Corresponding match types (direct/analogical/generic).
            steps_text: Steps text from the primary (highest-confidence) skill.
                        The caller is responsible for resolving this from the
                        match dict — no registry access needed here.
        """
        return cls(
            parent_skill_ids=parent_skill_ids,
            match_types=match_types,
            current_steps=steps_text,
            replaced_labels=[],
            discovered_landmarks=[],
            verification_notes=[],
            failed_assumptions=[],
            successful_adaptations=[],
        )
```

**No circular imports**: `DerivedSkillSession` has no imports from the skills package. It depends only on stdlib types. The orchestrator (caller) resolves `steps_text` from the match dict before calling `seed()`.

**Lifecycle**:
1. Created in `AutomationAgent.execute()` after `match()` returns candidates
2. Serialized into `skill_context` for `planner.plan()` and `planner.replan()`
3. Updated via `apply_patch()` after replan
4. Discarded when `execute()` returns

### 2.5 ReplanPatch

**File**: `src/automation_agent/shared_models.py`

**Rationale**: `ReplanPatch` has no skill-specific imports (pure data, stdlib types only). It belongs in `shared_models.py` alongside `ActionPlan` so the `replan_patch` field can be properly typed without cross-package imports.

```python
@dataclass
class ReplanPatch:
    """Patch to apply to a DerivedSkillSession after replanning.

    Parsed from the LLM replan response. All fields are optional because
    the LLM may not comply with the full schema.
    """

    replace_labels: list[dict] = field(default_factory=list)
        # [{"old": str, "new": str, "reason": str}]
    add_landmarks: list[str] = field(default_factory=list)
    verify_improvements: list[str] = field(default_factory=list)
    failed_assumptions: list[str] = field(default_factory=list)
    successful_adaptations: list[str] = field(default_factory=list)
    revised_steps: str = ""  # If non-empty, replaces current_steps

    @classmethod
    def from_dict(cls, data: dict) -> "ReplanPatch":
        """Parse a patch from LLM response dict. Tolerant of missing/malformed fields."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            replace_labels=[
                r for r in data.get("replace_labels", [])
                if isinstance(r, dict) and "old" in r and "new" in r
            ],
            add_landmarks=[
                str(lm) for lm in data.get("add_landmarks", [])
                if isinstance(lm, str)
            ],
            verify_improvements=[
                str(v) for v in data.get("verify_improvements", [])
                if isinstance(v, str)
            ],
            failed_assumptions=[
                str(f) for f in data.get("failed_assumptions", [])
                if isinstance(f, str)
            ],
            successful_adaptations=[
                str(s) for s in data.get("successful_adaptations", [])
                if isinstance(s, str)
            ],
            revised_steps=str(data.get("revised_steps", "")),
        )
```

**Example instance**:
```python
ReplanPatch(
    replace_labels=[
        {"old": "Orders", "new": "Purchase History", "reason": "Walmart uses 'Purchase History'"}
    ],
    add_landmarks=["Start a return", "Purchase History"],
    verify_improvements=["The Walmart purchase history page lists recent orders"],
    failed_assumptions=["Assumed 'Orders' link exists in nav bar"],
    successful_adaptations=["Found 'Purchase History' via account menu dropdown"],
)
```

### 2.6 SkillCardBuilder

**File**: `src/automation_agent/skills/card_builder.py` (new)

```python
class SkillCardBuilder:
    """Builds compact SkillCard objects from Skill dataclasses.

    Cards are computed on-the-fly, not persisted. Used by the router
    to build the LLM prompt.
    """

    @staticmethod
    def build(skill: Skill) -> SkillCard:
        """Build a SkillCard from a Skill object.

        Raises ValueError if the resulting card would have empty skill_id or summary.
        """
        skill_id = skill.skill_id or skill.name
        if not skill_id:
            raise ValueError("Cannot build SkillCard: empty skill_id and name")
        title = skill_id.replace("-", " ").replace("_", " ").title()
        summary = skill.summary or skill.description
        if not summary:
            raise ValueError(
                f"Cannot build SkillCard for '{skill_id}': empty summary and description"
            )
        tags = list(skill.tags) if skill.tags else list(skill.trigger_keywords)
        return SkillCard(
            skill_id=skill_id,
            title=title,
            summary=summary,
            tags=tags,
            required_apps=list(skill.requires.apps),
            required_os=skill.requires.os,
        )

    @classmethod
    def build_all(cls, skills: dict[str, Skill]) -> list[SkillCard]:
        """Build cards for all skills. Filters out skills that fail card building."""
        cards = []
        for skill in skills.values():
            try:
                cards.append(cls.build(skill))
            except Exception:
                continue
        return cards

    @staticmethod
    def format_cards_for_prompt(cards: list[SkillCard]) -> str:
        """Format skill cards as a compact text block for the router prompt."""
        parts = []
        for card in cards:
            lines = [
                f"### {card.skill_id}",
                f"**{card.title}**",
                card.summary,
            ]
            if card.tags:
                lines.append(f"Tags: {', '.join(card.tags)}")
            if card.required_apps:
                lines.append(f"Requires apps: {', '.join(card.required_apps)}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)
```

### 2.7 SkillMatchResult (replaces Dict return from match())

**File**: `src/automation_agent/shared_models.py`

The `SkillRegistry.match()` protocol return type changes from `Optional[Dict[str, Any]]` to `Optional["SkillMatchResult"]`. This follows the precedent set by `FindElementResult` — typed dataclass with dict-like `__getitem__`/`get()` compatibility shims for gradual migration.

```python
@dataclass
class SkillMatchResult:
    """Result of skill matching. Replaces the untyped Dict return."""

    skill_name: str                          # Primary (highest-confidence) skill name
    expanded_steps: str                      # Steps of primary skill with params substituted
    skill_context: str                       # Multi-skill context string
    params: Dict[str, str]                   # Extracted params for primary skill
    candidates: list[SkillRouteCandidate]    # All routing candidates

    def __getitem__(self, key: str) -> Any:
        """Dict-like access for backward compatibility during migration."""
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like .get() for backward compatibility during migration."""
        return getattr(self, key, default)
```

**Protocol update** (`protocols.py`):
```python
class SkillRegistry(Protocol):
    async def match(self, prompt: str) -> Optional["SkillMatchResult"]:
        ...
```

**Backward compatibility**: The `__getitem__` and `get()` shims mean existing orchestrator code like `skill_match["skill_name"]` and `skill_match.get("skill_context")` works unchanged. This matches the `FindElementResult` pattern already in `shared_models.py`.

**Confidence threshold**: `MIN_USEFUL_CONFIDENCE = 0.5` (constant in `skills/router.py`). If ALL candidates score below 0.5, `match()` returns `None`. The planner then produces a generic plan with no skill priors.

**Keyword fallback path**: When the LLM router is unavailable and keyword matching is used, the result is wrapped in a `SkillMatchResult` with `candidates` containing a single entry with `match_type=MatchType.DIRECT` and a **proportional confidence** computed as `matched_keywords / total_keywords` for that skill. For example, a prompt matching 1 of 4 trigger keywords gets `confidence=0.25` (below `MIN_USEFUL_CONFIDENCE`, so `match()` returns `None`). A prompt matching 3 of 4 gets `confidence=0.75`. This prevents false-positive keyword matches from being treated as high-confidence LLM-scored results.

The confidence computation lives in `registry.py` (the normalization layer), not in `matcher.py`. `matcher.py` is modified minimally: `match_skill()` returns the keyword score alongside the match so the registry can compute the ratio. Specifically, change `match_skill()` return type from `Optional[Tuple[Skill, Dict]]` to `Optional[Tuple[Skill, Dict, int]]` where the third element is the keyword hit count. The registry divides by `len(skill.trigger_keywords)` to get the confidence.

### 2.8 Multi-Skill Context String Assembly

The `skill_context` string passed to the planner is assembled by the registry and includes:

```
## Skill Priors

The following skills were selected as procedural priors for this task.
- **direct** matches may be followed closely.
- **analogical** matches are structural priors only — do NOT assume site-specific
  labels, buttons, or navigation are identical.
- **generic** matches are general-purpose priors — use only if more specific guidance is absent.

### [direct] return-amazon-order (confidence: 0.92)
Reason: Exact match for Amazon return flow.

Skill: return-amazon-order
Description: Return an item or package on Amazon
Success condition: Return confirmation with label or drop-off instructions visible

## Steps
1. Use open_url to navigate to ...
...

## Recovery Heuristics
...

## Observed Variants
- [alternative_path] When the return button is not visible, ...

---

### [analogical] open-app-and-navigate (confidence: 0.41)
Reason: Generic browser navigation prior.
...

---

## Derived Procedure (run-local, current best hypothesis)
Parent skill(s): return-amazon-order
### Label Replacements
- "Orders" -> "Purchase History" (Walmart uses 'Purchase History')
### Failed Assumptions (do NOT repeat)
- Assumed 'Orders' link exists in nav bar
```

---

## 3. Domain Slice Decomposition

### 3.1 Slice 1 — Foundation/Models (Engineer 1)

**New files**:
- `src/automation_agent/skills/card_builder.py` — `SkillCardBuilder` class
- `src/automation_agent/skills/derived_skill.py` — `DerivedSkillSession` class
- `tests/unit/test_card_builder.py`
- `tests/unit/test_derived_skill.py`

**Modified files**:
- `src/automation_agent/skills/models.py` — Add `SkillCard` dataclass; add optional `skill_id`, `tags`, `summary` to `Skill`
- `src/automation_agent/skills/loader.py` — Parse new optional frontmatter fields
- `src/automation_agent/shared_models.py` — Add `SkillRouteCandidate`, `SkillRouteResult`
- `src/automation_agent/skills/__init__.py` — Export new types

**Scope**:
1. Add `SkillCard` dataclass with fields per section 2.1
2. Add optional `skill_id`, `tags`, `summary` fields to `Skill` dataclass (section 2.2)
3. Update `loader.py:parse_skill_file()` to extract `skill-id`, `tags`, `summary` from frontmatter and pass to `Skill` constructor
4. Add `MatchType` (StrEnum), `SkillRouteCandidate`, `SkillRouteResult`, `SkillMatchResult`, and `ReplanPatch` to `shared_models.py` (sections 2.3, 2.5, 2.7). Add `replan_patch: Optional[ReplanPatch]` field to `ActionPlan`. Update `SkillRegistry.match()` return type in `protocols.py`.
5. Create `card_builder.py` with `SkillCardBuilder` (section 2.6)
6. Create `derived_skill.py` with `DerivedSkillSession` (section 2.4)
7. Update `__init__.py` to export `SkillCard`, `SkillCardBuilder`
8. Enrich all 8 skill library `.md` files with `skill-id`, `tags`, `summary` frontmatter

**Unit tests** (`tests/unit/test_card_builder.py`):
- `test_build_card_from_skill_with_all_fields` — skill with skill_id/tags/summary produces correct card
- `test_build_card_from_skill_without_optional_fields` — falls back to name/description
- `test_build_card_empty_skill_id_raises` — skill with empty name AND empty skill_id raises ValueError
- `test_build_card_empty_summary_raises` — skill with empty description AND empty summary raises ValueError
- `test_build_all_skips_failures` — malformed skill (empty id/summary) doesn't crash build_all, is silently skipped
- `test_format_cards_for_prompt` — output contains skill_id, summary, tags
- `test_card_builder_empty_skills` — empty dict returns empty list

**Unit tests** (`tests/unit/test_derived_skill.py`):
- `test_derived_skill_session_creation` — from_route_result produces valid session
- `test_apply_patch_extends_labels` — patch.replace_labels appended
- `test_apply_patch_extends_landmarks` — patch.add_landmarks appended
- `test_apply_patch_replaces_steps` — non-empty revised_steps replaces current_steps
- `test_apply_empty_patch` — empty patch is a no-op
- `test_serialize_for_context` — output contains all sections with correct headers
- `test_replan_patch_from_dict_valid` — well-formed dict parses correctly
- `test_replan_patch_from_dict_malformed` — missing keys produce empty defaults
- `test_replan_patch_from_dict_non_dict` — non-dict input returns empty patch
- `test_replan_patch_from_dict_extra_keys` — unknown keys silently ignored
- `test_replan_patch_from_dict_wrong_nesting` — e.g. `replace_labels` as string not list
- `test_replan_patch_empty_revised_steps` — empty string does NOT replace current_steps
- `test_apply_patch_idempotent` — applying same patch twice is a no-op (set semantics)
- `test_apply_patch_dedup_labels_by_old` — second patch for same "old" key overwrites first
- `test_apply_patch_caps_at_max_items` — lists never exceed _MAX_ITEMS=20
- `test_serialize_for_context_bounded_output` — output size proportional to _MAX_ITEMS
- `test_skill_route_candidate_validation` — invalid match_type raises ValueError (StrEnum)
- `test_match_type_strenum_values` — MatchType.DIRECT/ANALOGICAL/GENERIC have correct string values
- `test_skill_route_result_primary` — .primary returns highest-confidence
- `test_skill_route_result_empty` — .primary returns None on empty list
- `test_skill_match_result_getitem_compat` — `result["skill_name"]` works via shim
- `test_skill_match_result_get_compat` — `result.get("params", {})` works via shim

**Acceptance gates**:
- All 581+ existing tests pass
- New models round-trip correctly (construct -> serialize -> reconstruct)
- Existing skills load without new fields (backward compat)

**No dependencies on other slices.**

---

### 3.2 Slice 2 — Top-K Router (Engineer 2)

**Modified files**:
- `src/automation_agent/skills/router.py` — Top-k output, card-based summary
- `src/automation_agent/skills/prompts/route_skill.md` — Updated prompt
- `src/automation_agent/skills/registry.py` — `match()` returns new dict shape, multi-skill context assembly
- `src/automation_agent/skills/matcher.py` — Return keyword hit count alongside match (minimal change)

**Scope**:
1. Update `SkillRouter.__init__` to accept `list[SkillCard]` (built by registry)
2. Replace `_build_skills_summary()` with `SkillCardBuilder.format_cards_for_prompt()`
3. Update router prompt (`route_skill.md`) to request top-3 with `match_type`, `confidence`, `reason`
4. Update `_parse_response()` to parse `{"matches": [...]}` format with validation
5. Update `SkillRouter.route()` to return `Optional[SkillRouteResult]` (internal type)
6. Update `SkillRegistryImpl`:
   - Add `self._cards: list[SkillCard]` cached at load time (rebuilt in `load_from_directory()` and `load_from_string()` alongside the router rebuild)
   - `match()` passes cached `self._cards` to the router — no per-call card generation
   - Build multi-skill context string from top-k candidates
   - Return new dict shape (section 2.7)
   - Apply `MIN_USEFUL_CONFIDENCE = 0.5` threshold
7. Update keyword fallback path in `match()`:
   - `match_skill()` return type changes to include keyword hit count (third tuple element)
   - Registry computes `confidence = hit_count / len(skill.trigger_keywords)`
   - Wrap result in `SkillMatchResult` with `match_type=MatchType.DIRECT` and the proportional confidence
   - If confidence < `MIN_USEFUL_CONFIDENCE` (0.5), return `None` (weak keyword match rejected)
8. Add `build_multi_skill_context()` method to registry

**Updated router prompt** (`skills/prompts/route_skill.md`):
```markdown
You are a skill router for a macOS automation agent.

Given a user prompt and the available skill cards below, select the top 3 most
relevant skills (or fewer if fewer are relevant). For each, classify the match:

- **direct**: This skill is designed for exactly this task. Follow it closely.
- **analogical**: This skill has a similar procedural structure that can be adapted.
  Do NOT assume site-specific labels or buttons are identical.
- **generic**: This skill provides general utility (e.g., app navigation) that
  may help. Use only if no better match exists.

## Available Skills
{{skills_summary}}

## User Prompt
{{prompt}}

## Response
Respond with ONLY valid JSON (no markdown, no explanation):
{
  "matches": [
    {
      "skill_id": "skill-name",
      "match_type": "direct",
      "confidence": 0.95,
      "reason": "Brief explanation of why this skill matches",
      "params": {"param1": "value1"}
    }
  ]
}

If no skill is relevant at all, respond:
{"matches": []}

Rules:
- Return at most 3 matches, ordered by confidence (highest first).
- confidence is 0.0 to 1.0.
- Only include params for the highest-confidence match.
- If a skill is structurally similar but for a different site, label it "analogical".
```

**Unit tests** (`tests/unit/test_router_v2.py`):
- `test_route_returns_top_k_candidates` — mock LLM returns 3 matches, all parsed
- `test_route_single_direct_match` — one strong match returns single candidate
- `test_route_analogical_match` — "Walmart return" with amazon skill returns analogical
- `test_route_no_match_empty_list` — no relevant skill returns empty matches
- `test_route_parse_malformed_response` — garbled LLM output returns None
- `test_route_parse_missing_fields` — match missing confidence gets default
- `test_route_llm_failure_returns_none` — network error returns None gracefully
- `test_route_strips_markdown_fences` — ```json wrapper is handled
- `test_match_returns_new_dict_shape` — match() returns dict with `candidates` key
- `test_match_includes_skill_context` — multi-skill context string is non-empty
- `test_match_keyword_fallback_new_shape` — keyword fallback returns same dict shape
- `test_match_keyword_fallback_proportional_confidence` — 2/4 keyword hits produces confidence=0.5
- `test_match_keyword_fallback_weak_match_rejected` — 1/4 keyword hits produces confidence=0.25, below MIN_USEFUL_CONFIDENCE, returns None
- `test_match_keyword_fallback_strong_match_accepted` — 3/4 keyword hits produces confidence=0.75
- `test_match_filters_low_confidence` — all candidates below 0.5 returns None
- `test_match_none_when_no_skills` — empty registry returns None
- `test_multi_skill_context_includes_labels` — context string has [direct]/[analogical] markers
- `test_multi_skill_context_includes_observations` — learned observations appear per skill

**Acceptance gates**:
- All 581+ existing tests pass
- Router prompt is under 8000 tokens with all 8 current skills
- Keyword fallback still works when router is None

**Depends on**: Slice 1 (SkillCard, SkillRouteResult types)

---

### 3.3 Slice 3 — Multi-Skill Planner Context (Engineer 3)

**Modified files**:
- `src/automation_agent/planner/planner.py` — Parse optional `derived_skill_patch` from replan response
- `src/automation_agent/planner/prompts/plan_from_prompt.md` — Multi-skill priors section
- `src/automation_agent/planner/prompts/replan_from_state.md` — Derived procedure + patch request

**Scope**:
1. Update `plan_from_prompt.md`:
   - Replace the `## Skill Context (if available)` section with a structured `## Skill Priors` section
   - Add explicit instructions for direct vs analogical vs generic priors
   - The `{{skill_context}}` placeholder still works — the string now contains the structured multi-skill format
2. Update `replan_from_state.md`:
   - Add `## Derived Procedure` section using the `{{skill_context}}` placeholder (which now includes the serialized derived procedure)
   - Add instruction to return `derived_skill_patch` alongside `steps` in the JSON response
   - **MUST remove** the existing line "Same JSON format as before" (line 31) — it contradicts the new format. Replace with the explicit response format block below.
   - Keep backward compatibility: the response format adds an OPTIONAL `derived_skill_patch` key
3. Update `_parse_plan_response()`:
   - Extract `derived_skill_patch` from the parsed dict BEFORE the `steps` check — store it in a local variable
   - If `steps` key is missing, raise `ValueError` as today (the patch alone is not a valid replan response)
   - After extracting steps, parse the stored patch via `ReplanPatch.from_dict()` and attach to the `ActionPlan`
   - If patch is absent or malformed, set `replan_patch=None` (best-effort, never an error)
   - **Failure mode**: If the LLM returns `{"derived_skill_patch": {...}}` without `steps`, this is a parse failure — the replan is lost. This matches existing behavior (missing `steps` = ValueError). The patch is a supplement, not a substitute.
4. Add `replan_patch` optional field to `ActionPlan` dataclass

**Updated plan prompt** (`planner/prompts/plan_from_prompt.md`):
The `{{skill_context}}` placeholder is replaced at runtime with the multi-skill context string (section 2.8). No structural change to the placeholder — the content is richer.

Add after the existing skill context placeholder:
```markdown
### Guidance for Skill Priors
- **direct** matches: Follow the steps closely. The skill was designed for this exact task.
- **analogical** matches: Use the procedural structure as a guide, but do NOT assume
  site-specific labels, buttons, or navigation paths are identical. Adapt as needed.
- **generic** matches: Use only for general guidance. Do not rely on specific steps.
- If a **Derived Procedure** section is present, it represents corrections learned
  during this run. Prefer it over the original parent skill where they conflict.
```

**Updated replan prompt** (`planner/prompts/replan_from_state.md`):
**Replace** the entire `## Response Format` section (removing "Same JSON format as before"):
```markdown
## Response Format
Respond with ONLY valid JSON (no markdown, no explanation).
The "steps" key is REQUIRED. Every step MUST have a non-empty "verify" field.
Optionally include a "derived_skill_patch" if you discovered corrections
that should be remembered for the rest of this run:
```json
{
  "steps": [...],
  "derived_skill_patch": {
    "replace_labels": [{"old": "X", "new": "Y", "reason": "..."}],
    "add_landmarks": ["landmark text"],
    "verify_improvements": ["better verify condition"],
    "failed_assumptions": ["what did not work"],
    "successful_adaptations": ["what worked instead"]
  }
}
```
The "derived_skill_patch" field is optional. If you have no corrections, omit it.
```

**ActionPlan update** (`shared_models.py`):
```python
@dataclass
class ActionPlan:
    steps: List[ActionStep]
    goal: str = ""
    skill_name: Optional[str] = None
    raw_llm_response: Optional[str] = None
    planning_duration_ms: int = 0
    token_usage: Optional[Dict[str, int]] = None
    replan_patch: Optional["ReplanPatch"] = None  # Typed, same module
```

Since `ReplanPatch` is in the same file (`shared_models.py`), the forward reference resolves cleanly. No `Any` needed.

**Unit tests** (`tests/unit/test_planner_multiskill.py`):
- `test_plan_prompt_includes_skill_priors_guidance` — skill context with [direct] label appears in prompt
- `test_plan_prompt_no_skill_context` — "No skill context available" appears when None
- `test_replan_prompt_includes_derived_procedure` — derived procedure text appears in prompt
- `test_parse_replan_with_patch` — response with `derived_skill_patch` parsed to ActionPlan.replan_patch
- `test_parse_replan_without_patch` — response without patch produces ActionPlan.replan_patch=None
- `test_parse_replan_malformed_patch` — malformed patch dict produces empty ReplanPatch
- `test_parse_replan_patch_missing_fields` — partial patch dict handled gracefully
- `test_plan_still_works_without_multiskill` — existing plan behavior unchanged when skill_context is simple string
- `test_parse_replan_patch_only_no_steps` — response with `derived_skill_patch` but missing `steps` raises ValueError (patch is not a substitute for steps)
- `test_replan_prompt_no_same_format_line` — verify the actual replan prompt template does NOT contain "Same JSON format as before" (catches prompt drift)
- `test_parse_replan_extracts_patch_before_steps_check` — patch is captured even if steps validation fails (for logging/debugging)

**Acceptance gates**:
- All 581+ existing tests pass
- Plan/replan prompts render correctly with multi-skill context
- Missing or malformed patches never crash the planner

**Depends on**: Slice 1 (ReplanPatch type). Independent of Slice 2.

---

### 3.4 Slice 4 — Orchestrator Integration (Engineer 4)

**Modified files**:
- `src/automation_agent/orchestrator/agent.py` — DerivedSkillSession lifecycle, context assembly, replan patch integration

**Scope**:
1. In `execute()`, after `match()` returns:
   - Extract `candidates` from the match dict
   - Extract `steps_text` from primary skill's `expanded_steps` in the match dict
   - Create `derived_session = DerivedSkillSession.seed(parent_ids, match_types, steps_text)` as a **local variable**
   - Assemble `skill_context` string by calling `derived_session.serialize_for_context()` and appending to the multi-skill context from the match dict
2. Pass assembled `skill_context` to `planner.plan()`
3. In `_replan_and_continue(derived_session)` — **pass as argument**:
   - Re-assemble `skill_context` with updated derived procedure
   - Pass to `planner.replan()`
   - After replan, check `new_plan.replan_patch`
   - If present, call `derived_session.apply_patch(patch)`
   - Return `derived_session` (caller retains updated reference)
4. In `_maybe_learn_skill_run(derived_session)` — **pass as argument**:
   - Build a **distiller-specific context** string: the primary skill's original context (from `skill_match.expanded_steps`) PLUS the serialized derived procedure (from `derived_session.serialize_for_context()`). Do NOT pass the full multi-skill context to the distiller — the distiller prompt (`distill_skill_updates.md`) expects single-skill context under "Canonical Skill Context" and will get confused by multiple skill sections.
   - Pass this distiller-specific context as `skill_context` to `learn_from_run()`
5. No cleanup needed — `derived_session` is a local variable, GC'd when `execute()` returns

**Why local variable, not instance state**: `AutomationAgent` is async. If two `execute()` calls ran concurrently on the same instance, `self._derived_session` would be clobbered. A local variable scoped to `execute()` and threaded through as an argument eliminates the shared-state problem entirely. The method signatures gain one `Optional[DerivedSkillSession]` parameter (default `None`), which is internal — no protocol change.

**What does NOT change**:
- `AutomationAgent.__init__` signature (no new constructor params, no new instance state)
- `_handle_failure()` logic
- `_execute_step()` logic
- `StepVerifier` integration

**Distiller context isolation**: The distiller prompt template (`distill_skill_updates.md`) was designed for single-skill context. The `{{skill_context}}` placeholder receives one skill's steps, recovery heuristics, and observations. Passing the full multi-skill context (with 3 candidate skills + derived procedure) would confuse the distiller LLM about which skill to analyze. Therefore, `_maybe_learn_skill_run()` must construct a distiller-specific context containing only: (1) the primary skill's original steps/context, and (2) the derived procedure summary. No changes to the distiller prompt template itself are needed — the input is scoped correctly.

**Key integration detail — `_build_skill_fallback_plan()` must be updated**:

The `_build_skill_fallback_plan()` method at `agent.py:439` calls `_parse_skill_steps(skill_context)` which scans all lines for `^\d+\.\s+` patterns. With the new multi-skill context format, `skill_context` will contain numbered steps from multiple candidate skills plus derived procedure sections. Without changes, `_parse_skill_steps()` would concatenate steps from all skills into one plan (e.g., mixing Amazon and Walmart steps).

**Required fix in this slice**: Update `_build_skill_fallback_plan()` to extract steps only from the **primary (direct-match) skill section**. Approach:
1. Split `skill_context` on the `---` section separators used in the multi-skill format (section 2.8)
2. Identify the first/primary skill section (highest confidence, before the first `---`)
3. Pass only that section to `_parse_skill_steps()`
4. If no primary section is identifiable (e.g., only derived procedure), return `None` (no fallback plan)

This is a safety-critical fix: `_build_skill_fallback_plan()` is the safety net that catches trivial "done" plans from the LLM. If it silently breaks, the agent accepts trivial plans instead of executing skill steps.

**Unit tests** (`tests/unit/test_orchestrator_adaptive.py`):
- `test_execute_creates_derived_session` — after match with candidates, derived_session local is created and passed to planner
- `test_execute_no_match_no_session` — no skill match means no derived session, planner gets None skill_context
- `test_replan_applies_patch_to_session` — replan with patch updates session state via argument
- `test_replan_without_patch_session_unchanged` — replan without patch leaves session as-is
- `test_skill_context_includes_derived_procedure` — planner receives derived procedure text
- `test_concurrent_execute_no_clobber` — two concurrent execute() calls on same agent don't share state (local variable isolation)
- `test_learn_from_run_includes_derived_context` — distiller receives derived procedure in context via argument
- `test_learn_from_run_excludes_other_candidates` — distiller context does NOT contain analogical/generic skill sections (only primary + derived)
- `test_learn_from_run_primary_skill_only` — distiller's skill_context contains only the primary skill's expanded_steps, not multi-skill assembly
- `test_execute_with_analogical_match` — analogical match creates session with correct match_type
- `test_fallback_plan_extracts_primary_skill_only` — _build_skill_fallback_plan with multi-skill context extracts steps only from primary/direct-match section
- `test_fallback_plan_ignores_derived_procedure` — derived procedure section headers and content are not parsed as steps
- `test_fallback_plan_multi_skill_no_cross_contamination` — steps from analogical/generic candidates do NOT appear in fallback plan
- `test_fallback_plan_single_skill_unchanged` — single-skill context (backward compat) still works
- `test_existing_execute_flow_unchanged` — simple execute without skills works as before
- `test_replan_receives_session_as_argument` — _replan_and_continue receives derived_session as parameter, not from self

**Acceptance gates**:
- All 581+ existing tests pass
- Full execute -> replan -> patch -> learn flow works end-to-end in unit tests
- No new constructor params on AutomationAgent

**Depends on**: Slice 1 (DerivedSkillSession, ReplanPatch types). Benefits from Slice 2 and 3 but can be tested with mocks.

---

## 4. Error Messages (String Literals)

All error messages used in validation and logging:

```python
# SkillRouteCandidate / MatchType validation
# MatchType(value) raises ValueError automatically for invalid values (StrEnum behavior)

# ReplanPatch.from_dict logging
"Skipping malformed replace_label entry (missing 'old' or 'new')"

# SkillCardBuilder
"Skipping skill card build: empty skill_id and name"

# SkillRouter parse failure
"Could not parse router response as top-k JSON"
"Router returned unknown skill_id: {skill_id}"

# Registry match
"All routing candidates below MIN_USEFUL_CONFIDENCE ({threshold}), returning None"
"Skill routing returned {n} candidates"

# Orchestrator derived session
"Created DerivedSkillSession from {n} parent skill(s)"
"Applied ReplanPatch: {n_labels} label replacements, {n_landmarks} landmarks"
"No replan patch in LLM response, derived procedure unchanged"
```

---

## 5. Testing Strategy

### 5.1 Test Principles

1. **All test configs use `_env_file=None`** to prevent `.env` leaking `AGENT_MODEL_PROVIDER=anthropic`
2. **Mock LLM calls**, never hit real APIs in unit tests
3. **Use `AsyncMock`** for all async methods
4. **Each slice has independent unit tests** — no cross-slice test dependencies
5. **Baseline test count (recorded pre-implementation)**:
   - Unit tests: `pytest tests/unit/ --co -q` → **581 tests collected**
   - Total tests: `pytest --co -q` → **1200 tests collected** (2 collection errors in legacy files `test_llm_client.py`, `test_molmo_client.py`)
   - All 581 unit tests must pass after each slice. Run `pytest tests/unit/` as gate.
   - If any existing test is modified to accommodate new return shapes, it MUST be listed in the implementation PR description.

### 5.2 New Test Files

| File | Slice | Tests | What it covers |
|------|-------|-------|---------------|
| `tests/unit/test_card_builder.py` | 1 | ~7 | SkillCard building, validation, format_for_prompt |
| `tests/unit/test_derived_skill.py` | 1 | ~22 | DerivedSkillSession, ReplanPatch, SkillRouteCandidate, SkillMatchResult |
| `tests/unit/test_router_v2.py` | 2 | ~18 | Top-k routing, parse, match() new shape, keyword confidence |
| `tests/unit/test_planner_multiskill.py` | 3 | ~11 | Multi-skill prompt, replan patch parsing, prompt template validation |
| `tests/unit/test_orchestrator_adaptive.py` | 4 | ~16 | Full lifecycle, session management, concurrency, fallback isolation, distiller context |

**Total new tests**: ~74

### 5.3 Integration Tests

After all 4 slices merge:

| Test | What it verifies |
|------|-----------------|
| `test_analogical_match_flows_to_planner` | "Walmart return" with amazon skill -> planner sees [analogical] label |
| `test_replan_patch_updates_derived_procedure` | Replan produces patch -> session updated -> next context reflects change |
| `test_no_skill_match_still_plans` | No skills match -> planner works without skill context |
| `test_distiller_receives_derived_context` | Post-run distiller sees derived procedure in skill_context |

### 5.4 Existing Tests at Risk of Breaking

These existing tests assert on `match()` return dict keys or `skill_context` content. The `__getitem__`/`get()` shims on `SkillMatchResult` should keep them passing, but they must be verified during implementation:

| File | Lines | What it asserts | Risk |
|------|-------|----------------|------|
| `tests/unit/test_skill_registry.py` | 248-250 | `result["skill_name"]`, `result["params"]`, `result["expanded_steps"]` | LOW — shims cover these keys |
| `tests/unit/test_skill_registry.py` | 902-903, 932, 993-994 | `result["skill_name"]`, `result["params"]` (router parse tests) | LOW — router `_parse_response()` changes in Slice 2, tests must be updated for new format |
| `tests/unit/test_skill_learning.py` | 72-77 | `result["skill_context"]` content assertions | MEDIUM — skill_context content will be richer; assertions on "Recovery Heuristics" should still pass |
| `tests/unit/test_orchestrator_new.py` | 77-78, 92, 116-118, 155-157, 377-379, 413 | Mock `match()` return dicts, `skill_context` string assertions | LOW — mocks control the return value |
| `tests/integration/test_skill_learning_integration.py` | 89, 122 | `match["skill_context"]` | MEDIUM — integration test, depends on full context assembly |
| `tests/integration/test_status_and_skill_fallback_integration.py` | 67-68 | Mock match dict with `skill_name`, `params` | LOW — mock controls return |

**Router parse tests** (`test_skill_registry.py:900-994`): These test the current single-match `_parse_response()` format `{"skill_name": ..., "params": ...}`. Slice 2 changes the format to `{"matches": [...]}`. These tests MUST be updated in Slice 2 and the old tests either removed or adapted.

**Rule**: Any existing test modified during implementation must be listed in the PR description with the reason for the change.

---

## 6. Integration Points

### 6.1 Protocol Boundaries (from codebase.md)

| Protocol | Method | Change |
|----------|--------|--------|
| `SkillRegistry.match()` | `async (prompt) -> Optional[SkillMatchResult]` | Return type changes from `Optional[Dict]` to typed dataclass (dict-compat shims) |
| `ActionPlanner.plan()` | `..., skill_context: Optional[str]` | No signature change; richer string content |
| `ActionPlanner.replan()` | `..., skill_context: Optional[str]` | No signature change; richer string content |

### 6.2 Existing Code That Reads match() Output

These locations in `agent.py` read from the match dict and must work with the new shape:

| Line | Code | Works? |
|------|------|--------|
| 87 | `skill_match["skill_name"]` | Yes — `__getitem__` shim maps to `skill_match.skill_name` |
| 88 | `skill_match.get("params", {})` | Yes — `get()` shim maps to `skill_match.params` |
| 89 | `skill_match.get("skill_context") or skill_match.get("expanded_steps")` | Yes — both are typed fields |

New code (Slice 4) should use attribute access directly: `skill_match.candidates`, `skill_match.skill_name`. Dict-style access is a migration shim only.

### 6.3 Backward Compatibility Checklist

- [ ] Skills without `skill-id`, `tags`, `summary` load correctly
- [ ] `match()` returns `None` when no skills exist (unchanged)
- [ ] Keyword fallback produces same dict shape as LLM path
- [ ] `skill_context` string is still a valid, readable text block
- [ ] `ActionPlan.validate()` still works (new `replan_patch` field is optional)
- [ ] `_build_skill_fallback_plan()` extracts steps only from primary skill section in multi-skill context (no cross-contamination from analogical/generic candidates or derived procedure)
- [ ] `learn_from_run()` still works (receives richer skill_context string)

---

## 7. Constraints

1. **Python 3.11** target
2. **Line length 100** (Black + Ruff)
3. **Ruff rules**: E, W, F, I, B, C4, UP (ignores E501, B008)
4. **asyncio_mode = "auto"** in all test files
5. **`.env` gotcha**: Test configs MUST use `_env_file=None`
6. **Single replan per run** (orchestrator does not recurse). The derived procedure lifecycle in MVP is: seed at start -> planner sees it during replan -> updated once via patch -> serialized for distiller. The within-run value is providing richer replan context and structured correction data for post-run learning. "Corrections flowing across subsequent replans" requires multi-replan (post-MVP). Architecture supports this by design (`apply_patch` is idempotent/additive), but MVP does not deliver it.
7. **No new dependencies** — no embedding models, vector databases, or new pip packages
8. **Canonical skill files are never written at runtime** — hard invariant
9. **DerivedSkillSession is in-memory only** — not persisted between runs
10. **Router prompt budget**: The skill cards portion of the router prompt must stay under 4000 tokens. At ~100 tokens per card, this supports ~40 skills. When `len(self._cards) > 25`, emit a `WARNING` log: `"Skill count ({n}) approaching router prompt budget. Consider adding a shortlist/embedding pre-filter stage."` This is a soft warning, not a hard failure — the router will still work, but latency will degrade. The 25-skill threshold is chosen to give a ~15-skill buffer before the 4000-token ceiling.
11. **No latency SLA in MVP**: The router call latency depends on the backend (local Qwen ~45s baseline, Anthropic ~3s). The spec does not define a hard latency budget because the bottleneck is the backend, not the prompt size. The 25-skill warning provides early detection of prompt growth.
