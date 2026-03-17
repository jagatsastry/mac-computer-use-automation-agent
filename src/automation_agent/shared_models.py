"""Shared data models used across all components of the automation agent."""

from dataclasses import dataclass, field
from datetime import datetime
try:
    from enum import StrEnum
except ImportError:  # Python < 3.11
    from enum import Enum

    class StrEnum(str, Enum):  # type: ignore[no-redef]
        def __str__(self) -> str:
            return self.value
from typing import Any, Dict, List, Optional

# AC-1/AC-2: Canonical AX roles for text input fields.
# Shared between verifier (Tier 0 click check) and orchestrator (type-and-check bypass).
TEXT_INPUT_AX_ROLES: frozenset[str] = frozenset({
    "AXTextField",
    "AXTextArea",
    "AXSearchField",
    "AXComboBox",
})

# AC-1: Heuristic fallback for when AX is unavailable. English-only; expand for i18n.
TEXT_INPUT_KEYWORDS: frozenset[str] = frozenset({
    "search", "input", "text field", "text box",
    "search bar", "address bar", "url bar",
})

# Common LLM misspellings → correct on_fail value.
# Used by ActionStep.from_dict() to normalize invalid on_fail strings.
_ON_FAIL_ALIASES: Dict[str, str] = {
    "scroll": "retry_different",
    "retry": "retry_different",
    "skip": "abort",
    "abort_reason": "abort",
    "continue": "retry_different",
    "fail": "abort",
    "stop": "abort",
}

# Common LLM misspellings → correct action name.
# Used by ActionStep.from_dict() to auto-correct invalid action names.
_ACTION_ALIASES: Dict[str, str] = {
    "key_press": "press_key",
    "keypress": "press_key",
    "send_keys": "press_key",
    "send_key": "press_key",
    "press": "press_key",
    "typetext": "type_text",
    "type": "type_text",
    "enter_text": "type_text",
    "fill": "type_text",
    "fill_in": "type_text",
    "input": "type_text",
    "write": "type_text",
    "launch_app": "activate_app",
    "open_app": "activate_app",
    "start_app": "activate_app",
    "close_app": "quit_app",
    "navigate": "open_url",
    "goto_url": "open_url",
    "go_to_url": "open_url",
    "go_to": "open_url",
    "browse": "open_url",
    "visit": "open_url",
    "find_element": "click",
    "select": "click",
    "choose": "click",
    "tap": "click",
    "pick": "click",
    "submit": "click",
    "submit_form": "click",
    "scroll_down": "scroll",
    "scroll_up": "scroll",
    "look": "observe",
    "check": "observe",
    "inspect": "observe",
    "wait": "wait_for_user",
    "finish": "done",
    "complete": "done",
    "end": "done",
    "stop": "done",
}


@dataclass
class FindElementResult:
    """Result of locating a UI element on screen.

    Returned by ScreenCoordinator.find_element() and AutomationAgent._find_element().
    """

    x: int
    y: int
    confidence: float = 0.0  # 0.0 = unknown/not reported, 1.0 = certain
    source: str = ""         # "accessibility", "vision", "grounding"
    raw_response: str = ""
    screen_x: Optional[int] = None
    screen_y: Optional[int] = None
    image_width: int = 0
    image_height: int = 0

    def __getitem__(self, key: str) -> Any:
        """Provide dict-like compatibility for older callers/tests."""
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        """Provide dict-like compatibility for older callers/tests."""
        return getattr(self, key, default)


@dataclass
class ActionStep:
    """A single action step in an execution plan.

    Every step MUST have a non-empty 'verify' field describing
    the expected screen state after the step executes.
    """

    action: str  # "click", "type_text", "press_key", "open_url", "activate_app", "scroll", "observe", "wait_for_user", "done"
    params: Dict[str, Any] = field(default_factory=dict)
    verify: str = ""  # MANDATORY — what must be true after this step
    expected_observation: str = ""  # Optional stronger visual expectation for Tier 2 verification
    on_fail: str = "retry_different"  # "retry_different" | "replan" | "abort" | "wait_for_user"
    max_retries: int = 3
    destructive: bool = False  # AC-6b: optional planner flag for irreversible actions

    def __post_init__(self) -> None:
        valid_actions = {
            "click",
            "type_text",
            "press_key",
            "open_url",
            "activate_app",
            "quit_app",
            "scroll",
            "observe",
            "wait_for_user",
            "done",
        }
        if self.action and self.action not in valid_actions:
            raise ValueError(
                f"Unknown action '{self.action}'. Valid actions: {sorted(valid_actions)}"
            )
        valid_on_fail = {"retry_different", "replan", "abort", "wait_for_user"}
        if self.on_fail not in valid_on_fail:
            raise ValueError(
                f"Unknown on_fail '{self.on_fail}'. Valid: {sorted(valid_on_fail)}"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActionStep":
        """Create ActionStep from dictionary (e.g., parsed LLM JSON).

        Automatically corrects common LLM action name misspellings
        (e.g., 'key_press' → 'press_key').
        Also infers scroll direction from aliases like 'scroll_down' → 'scroll'.
        """
        raw_action = data.get("action", "")
        action = _ACTION_ALIASES.get(raw_action, raw_action)
        params = data.get("params", {})
        # Inject direction for scroll aliases when not explicitly provided
        if action == "scroll" and "direction" not in params:
            if raw_action == "scroll_down":
                params = {**params, "direction": "down"}
            elif raw_action == "scroll_up":
                params = {**params, "direction": "up"}
        # Normalize on_fail: coerce non-string types, then map aliases
        raw_on_fail = data.get("on_fail", "retry_different")
        if not isinstance(raw_on_fail, str):
            raw_on_fail = "retry_different"
        _valid_on_fail = {"retry_different", "replan", "abort", "wait_for_user"}
        if raw_on_fail not in _valid_on_fail:
            raw_on_fail = _ON_FAIL_ALIASES.get(raw_on_fail, "retry_different")

        return cls(
            action=action,
            params=params,
            verify=data.get("verify", ""),
            expected_observation=data.get("expected_observation", ""),
            on_fail=raw_on_fail,
            max_retries=data.get("max_retries", 3),
            destructive=bool(data.get("destructive", False)),
        )


@dataclass
class ReplanPatch:
    """Patch to apply to a DerivedSkillSession after replanning.

    Parsed from the LLM replan response. All fields are optional because
    the LLM may not comply with the full schema.
    """

    replace_labels: List[Dict[str, str]] = field(default_factory=list)
    add_landmarks: List[str] = field(default_factory=list)
    verify_improvements: List[str] = field(default_factory=list)
    failed_assumptions: List[str] = field(default_factory=list)
    successful_adaptations: List[str] = field(default_factory=list)
    revised_steps: str = ""

    def is_empty(self) -> bool:
        """Return True if this patch has no usable content."""
        return (
            not self.replace_labels
            and not self.add_landmarks
            and not self.verify_improvements
            and not self.failed_assumptions
            and not self.successful_adaptations
            and not self.revised_steps
        )

    @classmethod
    def from_dict(cls, data: dict) -> Optional["ReplanPatch"]:
        """Parse a patch from LLM response dict. Tolerant of missing/malformed fields.

        Returns None for non-dict input or patches with no usable content.
        """
        if not isinstance(data, dict):
            return None

        def _safe_list(key: str) -> list:
            val = data.get(key, [])
            return val if isinstance(val, list) else []

        def _safe_str(val: object) -> str:
            """Convert to string, treating None/non-str as empty."""
            if val is None or not isinstance(val, str):
                return ""
            return val.strip()

        patch = cls(
            replace_labels=[
                r for r in _safe_list("replace_labels")
                if isinstance(r, dict) and "old" in r and "new" in r
            ],
            add_landmarks=[
                str(lm) for lm in _safe_list("add_landmarks")
                if isinstance(lm, str)
            ],
            verify_improvements=[
                str(v) for v in _safe_list("verify_improvements")
                if isinstance(v, str)
            ],
            failed_assumptions=[
                str(f) for f in _safe_list("failed_assumptions")
                if isinstance(f, str)
            ],
            successful_adaptations=[
                str(s) for s in _safe_list("successful_adaptations")
                if isinstance(s, str)
            ],
            revised_steps=_safe_str(data.get("revised_steps")),
        )
        return None if patch.is_empty() else patch


@dataclass
class ActionPlan:
    """A plan consisting of ordered action steps."""

    steps: List[ActionStep]
    goal: str = ""
    skill_name: Optional[str] = None
    raw_llm_response: Optional[str] = None
    planning_duration_ms: int = 0
    token_usage: Optional[Dict[str, int]] = None
    replan_patch: Optional["ReplanPatch"] = None

    def validate(self) -> List[str]:
        """Validate the plan. Returns list of error messages (empty if valid)."""
        errors = []
        if not self.steps:
            errors.append("Plan has no steps")
        for i, step in enumerate(self.steps):
            if not step.verify and step.action not in ("done", "wait_for_user", "observe"):
                errors.append(
                    f"Step {i} ({step.action}) has empty 'verify' field — "
                    "every step must have a postcondition"
                )
        return errors


@dataclass
class StepResult:
    """Result of executing and verifying a single action step."""

    step: ActionStep
    success: bool
    verification_method: str = ""  # "accessibility" | "actuator_state" | "vision" | "both"
    evidence: str = ""  # MANDATORY — what was observed
    error: Optional[str] = None
    duration_ms: int = 0
    screenshot_path: Optional[str] = None
    retry_count: int = 0
    retry_strategies_used: List[str] = field(default_factory=list)
    reflection_hint: str = ""
    reflection_observed: str = ""
    suggested_element: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    pre_state_app: str = ""  # Frontmost app before action
    post_state_app: str = ""  # Frontmost app after action
    pre_state_url: str = ""  # Browser URL before action
    post_state_url: str = ""  # Browser URL after action

    def __post_init__(self) -> None:
        valid_methods = {
            "", "accessibility", "actuator_state", "vision", "both",
            "type_and_check", "lookahead",
            "scroll_recovery", "scroll_recovery_verified",
        }
        if self.verification_method not in valid_methods:
            raise ValueError(
                f"Unknown verification_method '{self.verification_method}'. "
                f"Valid: {sorted(valid_methods)}"
            )


@dataclass
class ExecutionResult:
    """Result of executing an entire task (all steps)."""

    success: bool
    message: str = ""
    steps: List[StepResult] = field(default_factory=list)
    error: Optional[str] = None
    total_duration_ms: int = 0
    iterations: int = 0
    goal: str = ""
    run_id: str = ""
    infeasibility_reason: Optional[str] = None  # AC-3: human-readable explanation


# ---------------------------------------------------------------------------
# Adaptive Skill System types (top-k routing)
# ---------------------------------------------------------------------------


class MatchType(StrEnum):
    """How a skill relates to the user prompt."""

    DIRECT = "direct"
    ANALOGICAL = "analogical"
    GENERIC = "generic"


@dataclass
class SkillRouteCandidate:
    """A single candidate skill from the router."""

    skill_id: str
    match_type: MatchType
    confidence: float
    reason: str

    def __post_init__(self) -> None:
        if isinstance(self.match_type, str):
            self.match_type = MatchType(self.match_type)
        self.confidence = max(0.0, min(1.0, self.confidence))


@dataclass
class SkillRouteResult:
    """Result of top-k skill routing."""

    candidates: list[SkillRouteCandidate]
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def primary(self) -> Optional[SkillRouteCandidate]:
        """Highest-confidence candidate, or None if empty."""
        return self.candidates[0] if self.candidates else None

    @property
    def has_direct_match(self) -> bool:
        return any(c.match_type == MatchType.DIRECT for c in self.candidates)


@dataclass
class SkillMatchResult:
    """Result of skill matching. Replaces the untyped Dict return."""

    skill_name: str
    expanded_steps: str
    skill_context: str
    params: Dict[str, str]
    candidates: list[SkillRouteCandidate]

    def __getitem__(self, key: str) -> Any:
        """Dict-like access for backward compatibility during migration."""
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like .get() for backward compatibility during migration."""
        return getattr(self, key, default)
