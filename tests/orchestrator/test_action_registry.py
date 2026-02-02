"""Tests for action registry component."""

import pytest
from automation_agent.orchestrator.action_registry import ActionRegistry
from automation_agent.actions.applescript import (
    ActivateAppAction,
    OpenURLAction,
    QuitAppAction,
    TypeTextAction,
    PressKeyAction,
    ClickUIElementAction,
)
from automation_agent.actions.simple import ClickAction


class TestActionRegistryInit:
    """Test ActionRegistry initialization."""

    def test_init_has_default_actions(self):
        """Test registry initializes with default actions."""
        registry = ActionRegistry()

        assert registry.has_action("activate_app")
        assert registry.has_action("open_url")
        assert registry.has_action("quit_app")
        assert registry.has_action("type_text")
        assert registry.has_action("press_key")
        assert registry.has_action("click_ui_element")
        assert registry.has_action("click")

    def test_list_actions(self):
        """Test listing all registered actions."""
        registry = ActionRegistry()
        actions = registry.list_actions()

        assert "activate_app" in actions
        assert "open_url" in actions
        assert "quit_app" in actions
        assert "type_text" in actions
        assert "press_key" in actions
        assert len(actions) >= 7


class TestActionRegistryRegistration:
    """Test action registration and unregistration."""

    def test_register_new_action(self):
        """Test registering a new action type."""
        registry = ActionRegistry()

        class CustomAction:
            pass

        registry.register("custom_action", CustomAction)

        assert registry.has_action("custom_action")
        assert "custom_action" in registry.list_actions()

    def test_register_overwrites_existing(self):
        """Test that registering overwrites existing action."""
        registry = ActionRegistry()

        class NewActivateApp:
            pass

        registry.register("activate_app", NewActivateApp)
        # The action class should be updated
        assert registry._actions["activate_app"] == NewActivateApp

    def test_unregister_action(self):
        """Test unregistering an action."""
        registry = ActionRegistry()
        registry.unregister("quit_app")

        assert not registry.has_action("quit_app")
        assert "quit_app" not in registry.list_actions()

    def test_unregister_nonexistent_action(self):
        """Test unregistering a non-existent action doesn't raise."""
        registry = ActionRegistry()
        # Should not raise
        registry.unregister("nonexistent_action")


class TestActionRegistryGetAction:
    """Test getting and instantiating actions."""

    def test_get_activate_app_action(self):
        """Test getting activate_app action."""
        registry = ActionRegistry()
        action = registry.get_action("activate_app", {"app_name": "Safari"})

        assert isinstance(action, ActivateAppAction)
        assert action.app_name == "Safari"

    def test_get_open_url_action(self):
        """Test getting open_url action."""
        registry = ActionRegistry()
        action = registry.get_action("open_url", {
            "url": "https://google.com",
            "browser": "Chrome"
        })

        assert isinstance(action, OpenURLAction)
        assert action.url == "https://google.com"
        assert action.browser == "Chrome"

    def test_get_open_url_action_default_browser(self):
        """Test open_url action with default browser."""
        registry = ActionRegistry()
        action = registry.get_action("open_url", {"url": "https://google.com"})

        assert isinstance(action, OpenURLAction)
        assert action.browser == "Safari"  # Default

    def test_get_quit_app_action(self):
        """Test getting quit_app action."""
        registry = ActionRegistry()
        action = registry.get_action("quit_app", {"app_name": "Terminal"})

        assert isinstance(action, QuitAppAction)
        assert action.app_name == "Terminal"

    def test_get_type_text_action(self):
        """Test getting type_text action."""
        registry = ActionRegistry()
        action = registry.get_action("type_text", {"text": "Hello World"})

        assert isinstance(action, TypeTextAction)
        assert action.text == "Hello World"

    def test_get_press_key_action(self):
        """Test getting press_key action."""
        registry = ActionRegistry()
        action = registry.get_action("press_key", {"keys": ["command", "c"]})

        assert isinstance(action, PressKeyAction)
        assert action.keys == ["command", "c"]

    def test_get_press_key_action_single_key(self):
        """Test press_key action with single key."""
        registry = ActionRegistry()
        action = registry.get_action("press_key", {"keys": ["return"]})

        assert isinstance(action, PressKeyAction)
        assert action.keys == ["return"]

    def test_get_click_ui_element_action(self):
        """Test getting click_ui_element action."""
        registry = ActionRegistry()
        action = registry.get_action("click_ui_element", {
            "app_name": "Safari",
            "element_description": 'button "OK"'
        })

        assert isinstance(action, ClickUIElementAction)
        assert action.app_name == "Safari"
        assert action.element_description == 'button "OK"'

    def test_get_click_action(self):
        """Test getting click action."""
        registry = ActionRegistry()
        action = registry.get_action("click", {"x": 100, "y": 200})

        assert isinstance(action, ClickAction)
        assert action.x == 100
        assert action.y == 200

    def test_get_unknown_action_returns_none(self):
        """Test getting unknown action returns None."""
        registry = ActionRegistry()
        action = registry.get_action("nonexistent", {})

        assert action is None

    def test_get_action_with_empty_params(self):
        """Test getting action with empty params."""
        registry = ActionRegistry()
        action = registry.get_action("activate_app", {})

        assert isinstance(action, ActivateAppAction)
        assert action.app_name == ""

    def test_get_action_with_missing_params(self):
        """Test getting action with partially missing params."""
        registry = ActionRegistry()
        action = registry.get_action("open_url", {"url": "https://test.com"})

        assert isinstance(action, OpenURLAction)
        assert action.url == "https://test.com"
        assert action.browser == "Safari"  # Default


class TestActionRegistryHasAction:
    """Test has_action method."""

    def test_has_existing_action(self):
        """Test has_action returns True for existing action."""
        registry = ActionRegistry()
        assert registry.has_action("activate_app") is True

    def test_has_nonexistent_action(self):
        """Test has_action returns False for non-existent action."""
        registry = ActionRegistry()
        assert registry.has_action("fly_to_moon") is False

    def test_has_action_after_registration(self):
        """Test has_action after registering new action."""
        registry = ActionRegistry()

        class NewAction:
            pass

        assert registry.has_action("new_action") is False
        registry.register("new_action", NewAction)
        assert registry.has_action("new_action") is True

    def test_has_action_after_unregistration(self):
        """Test has_action after unregistering action."""
        registry = ActionRegistry()
        assert registry.has_action("quit_app") is True
        registry.unregister("quit_app")
        assert registry.has_action("quit_app") is False


class TestActionRegistryEdgeCases:
    """Test edge cases and special scenarios."""

    def test_multiple_registries_are_independent(self):
        """Test that multiple registry instances are independent."""
        registry1 = ActionRegistry()
        registry2 = ActionRegistry()

        class CustomAction:
            pass

        registry1.register("custom", CustomAction)

        assert registry1.has_action("custom")
        assert not registry2.has_action("custom")

    def test_action_with_extra_params_ignored(self):
        """Test that extra params are handled gracefully."""
        registry = ActionRegistry()
        action = registry.get_action("activate_app", {
            "app_name": "Safari",
            "extra_param": "ignored",
            "another": 123
        })

        assert isinstance(action, ActivateAppAction)
        assert action.app_name == "Safari"

    def test_get_action_preserves_param_types(self):
        """Test that param types are preserved."""
        registry = ActionRegistry()
        action = registry.get_action("click", {"x": 100, "y": 200})

        assert isinstance(action.x, int)
        assert isinstance(action.y, int)

    def test_type_text_with_special_characters(self):
        """Test type_text action with special characters."""
        registry = ActionRegistry()
        action = registry.get_action("type_text", {"text": 'Hello "World" & <test>'})

        assert isinstance(action, TypeTextAction)
        assert action.text == 'Hello "World" & <test>'

    def test_press_key_with_empty_keys(self):
        """Test press_key action with empty keys list."""
        registry = ActionRegistry()
        action = registry.get_action("press_key", {"keys": []})

        assert isinstance(action, PressKeyAction)
        assert action.keys == []
