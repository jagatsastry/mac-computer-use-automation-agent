"""Protocol classes defining component interfaces.

Each protocol defines the contract a component must satisfy.
Components depend on protocols, not concrete implementations.
"""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from automation_agent.shared_models import ActionPlan, ActionStep, ExecutionResult, StepResult


@runtime_checkable
class ActionPlanner(Protocol):
    """Plans multi-step action sequences from natural language goals."""

    async def plan(
        self,
        goal: str,
        screen_description: str = "",
        skill_context: Optional[str] = None,
    ) -> ActionPlan:
        """Generate an action plan for the given goal.

        Args:
            goal: Natural language description of what to accomplish.
            screen_description: Current screen state description.
            skill_context: Optional expanded skill template for context.

        Returns:
            ActionPlan with validated steps (all steps have verify fields).
        """
        ...

    async def replan(
        self,
        goal: str,
        screen_description: str,
        history: List[StepResult],
        retry_strategies_used: List[str],
    ) -> ActionPlan:
        """Generate a new plan given execution history and failures.

        Must produce a DIFFERENT approach than what was already tried.

        Args:
            goal: Original goal.
            screen_description: Current screen state.
            history: Results of previously executed steps.
            retry_strategies_used: Strategies already attempted.

        Returns:
            ActionPlan with a different approach.
        """
        ...


@runtime_checkable
class ScreenCoordinator(Protocol):
    """Coordinates vision model queries for screen understanding."""

    async def find_element(
        self, description: str, screenshot_b64: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Find a UI element on screen by description.

        Returns dict with 'x', 'y' pixel coordinates, or None if not found.
        """
        ...

    async def describe_screen(
        self,
        screenshot_b64: Optional[str] = None,
        hammerspoon_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Describe the current screen state in natural language."""
        ...

    async def verify_condition(
        self, condition: str, screenshot_b64: Optional[str] = None
    ) -> bool:
        """Check if a visual condition is true on the current screen."""
        ...

    async def capture_screenshot(self) -> str:
        """Capture and return a base64-encoded screenshot."""
        ...


@runtime_checkable
class Actuator(Protocol):
    """Executes low-level actions on the desktop."""

    def is_available(self) -> bool:
        """Check if the actuator backend is available."""
        ...

    def click(self, x: int, y: int) -> Dict[str, Any]:
        """Click at screen coordinates. Returns result dict with 'success' and optional 'error'."""
        ...

    def type_text(self, text: str) -> Dict[str, Any]:
        """Type text. Returns result dict."""
        ...

    def press_key(self, keys: List[str]) -> Dict[str, Any]:
        """Press key combination. Returns result dict."""
        ...

    def activate_app(self, app_name: str) -> Dict[str, Any]:
        """Activate/launch an application. Returns result dict."""
        ...

    def open_url(self, url: str) -> Dict[str, Any]:
        """Open a URL. Returns result dict."""
        ...

    def quit_app(self, app_name: str) -> Dict[str, Any]:
        """Quit an application. Returns result dict."""
        ...

    def get_state(self) -> Dict[str, Any]:
        """Get current desktop state (frontmost app, window title, etc.)."""
        ...


@runtime_checkable
class SkillRegistry(Protocol):
    """Manages skill templates for common automation tasks."""

    def match(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Find a matching skill for the given prompt.

        Returns dict with 'skill_name', 'expanded_steps', 'params', or None.
        """
        ...

    def list_skills(self) -> List[Dict[str, str]]:
        """List all available skills with name and description."""
        ...

    def expand(self, skill_name: str, params: Dict[str, str]) -> Optional[str]:
        """Expand a skill template with parameters.

        Returns the expanded skill text, or None if skill not found.
        """
        ...

    def validate_all(self) -> List[str]:
        """Validate all loaded skills. Returns list of error messages."""
        ...


@runtime_checkable
class Verifier(Protocol):
    """Verifies that action steps achieved their intended effect."""

    async def verify(
        self,
        step: ActionStep,
        actuator_result: Dict[str, Any],
        coordinator: Optional[ScreenCoordinator] = None,
        actuator: Optional[Actuator] = None,
    ) -> StepResult:
        """Verify a step's postcondition using tiered verification.

        Tier 0: Accessibility API state (if applicable)
        Tier 1: Hammerspoon state query (~50ms)
        Tier 2: Vision screenshot verification (~2-5s)

        Returns StepResult with verification_method and evidence.
        """
        ...
