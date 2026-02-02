"""Action registry for mapping action types to executable actions."""

from typing import Any, Dict, Optional, Type

from ..actions.applescript import (
    ActivateAppAction,
    AppleScriptAction,
    OpenURLAction,
    QuitAppAction,
    TypeTextAction,
    PressKeyAction,
    ClickUIElementAction,
)
from ..actions.simple import ClickAction


class ActionRegistry:
    """
    Registry that maps action type names to action classes.

    Provides a central place to look up and instantiate actions
    based on the action type string from parsed intents.
    """

    # Default action mappings
    _default_actions: Dict[str, Type] = {
        "activate_app": ActivateAppAction,
        "open_url": OpenURLAction,
        "quit_app": QuitAppAction,
        "type_text": TypeTextAction,
        "press_key": PressKeyAction,
        "click_ui_element": ClickUIElementAction,
        "click": ClickAction,
    }

    def __init__(self):
        """Initialize action registry with default actions."""
        self._actions: Dict[str, Type] = self._default_actions.copy()

    def register(self, action_type: str, action_class: Type) -> None:
        """
        Register a new action type.

        Args:
            action_type: String identifier for the action
            action_class: Class to instantiate for this action type
        """
        self._actions[action_type] = action_class

    def unregister(self, action_type: str) -> None:
        """
        Remove an action type from the registry.

        Args:
            action_type: String identifier to remove
        """
        self._actions.pop(action_type, None)

    def get_action(
        self, action_type: str, params: Dict[str, Any]
    ) -> Optional[AppleScriptAction]:
        """
        Get an action instance for the given type and parameters.

        Args:
            action_type: String identifier for the action
            params: Parameters to pass to the action constructor

        Returns:
            Instantiated action object, or None if action type not found
        """
        action_class = self._actions.get(action_type)
        if action_class is None:
            return None

        # Map parameter names to constructor arguments
        return self._instantiate_action(action_class, action_type, params)

    def _instantiate_action(
        self, action_class: Type, action_type: str, params: Dict[str, Any]
    ) -> Any:
        """
        Instantiate an action with the correct parameters.

        Different actions have different constructor signatures,
        so we map the params dict to the expected arguments.
        """
        if action_type == "activate_app":
            return action_class(app_name=params.get("app_name", ""))

        elif action_type == "open_url":
            return action_class(
                url=params.get("url", ""),
                browser=params.get("browser", "Safari"),
            )

        elif action_type == "quit_app":
            return action_class(app_name=params.get("app_name", ""))

        elif action_type == "type_text":
            return action_class(text=params.get("text", ""))

        elif action_type == "press_key":
            keys = params.get("keys", [])
            return action_class(keys=keys)

        elif action_type == "click_ui_element":
            return action_class(
                app_name=params.get("app_name", ""),
                element_description=params.get("element_description", ""),
            )

        elif action_type == "click":
            return action_class(
                x=params.get("x", 0),
                y=params.get("y", 0),
            )

        # Generic fallback - try to instantiate with params as kwargs
        try:
            return action_class(**params)
        except TypeError:
            return action_class()

    def list_actions(self) -> list:
        """List all registered action types."""
        return list(self._actions.keys())

    def has_action(self, action_type: str) -> bool:
        """Check if an action type is registered."""
        return action_type in self._actions
