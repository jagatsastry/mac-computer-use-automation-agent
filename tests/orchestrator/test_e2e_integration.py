"""
End-to-End Integration Tests for macOS Automation Agent.

These tests simulate complete automation workflows from user prompt
to action execution, covering both simple and complex scenarios.
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.orchestrator.intent_parser import IntentParser
from automation_agent.orchestrator.action_registry import ActionRegistry
from automation_agent.orchestrator.observer import ScreenObserver
from automation_agent.orchestrator.models import (
    Intent,
    ActionStep,
    Observation,
    Coordinates,
)
from automation_agent.actions.applescript import AppleScriptResult


# ============================================================================
# Fixtures for E2E tests
# ============================================================================

@pytest.fixture
def e2e_ollama_client():
    """Create a mock Ollama client for E2E tests."""
    client = MagicMock()
    client.generate = AsyncMock()
    client.generate_vision = AsyncMock()
    client.test_connection = AsyncMock(return_value=True)
    return client


@pytest.fixture
def e2e_screen_capturer():
    """Create a mock screen capturer for E2E tests."""
    capturer = MagicMock()
    capturer.capture_screen_b64 = MagicMock(return_value="mock_screenshot_base64")
    capturer.get_screen_size = MagicMock(return_value=(1920, 1080))
    return capturer


@pytest.fixture
def e2e_agent(e2e_ollama_client, e2e_screen_capturer):
    """Create a fully configured agent for E2E tests."""
    parser = IntentParser(e2e_ollama_client, model="gemma2:9b")
    observer = ScreenObserver(e2e_ollama_client, e2e_screen_capturer, model="qwen3-vl")
    registry = ActionRegistry()

    agent = AutomationAgent(
        parser=parser,
        observer=observer,
        registry=registry,
        llm_client=e2e_ollama_client,
        text_model="gemma2:9b",
        max_iterations=10,
        action_delay=0,  # No delay for tests
    )
    return agent


# ============================================================================
# Simple Task E2E Tests
# ============================================================================

class TestE2ESimpleTasks:
    """E2E tests for simple automation tasks (no vision needed)."""

    @pytest.mark.asyncio
    async def test_e2e_open_safari(self, e2e_agent, e2e_ollama_client):
        """E2E: Open Safari browser."""
        # Configure LLM response for intent parsing
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [{"action": "activate_app", "params": {"app_name": "Safari"}}],
            "requires_observation": False
        }))

        # Mock AppleScript execution
        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            result = await e2e_agent.execute("Open Safari")

            assert result.success is True
            assert len(result.steps) == 1
            assert result.steps[0].action == "activate_app"

    @pytest.mark.asyncio
    async def test_e2e_open_url_in_browser(self, e2e_agent, e2e_ollama_client):
        """E2E: Open YouTube in Safari."""
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [{
                "action": "open_url",
                "params": {"url": "https://www.youtube.com", "browser": "Safari"}
            }],
            "requires_observation": False
        }))

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            result = await e2e_agent.execute("Open YouTube in Safari")

            assert result.success is True
            assert result.steps[0].action == "open_url"

    @pytest.mark.asyncio
    async def test_e2e_quit_application(self, e2e_agent, e2e_ollama_client):
        """E2E: Quit Chrome browser."""
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [{"action": "quit_app", "params": {"app_name": "Google Chrome"}}],
            "requires_observation": False
        }))

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            result = await e2e_agent.execute("Close Chrome")

            assert result.success is True
            assert result.steps[0].params["app_name"] == "Google Chrome"

    @pytest.mark.asyncio
    async def test_e2e_multi_step_navigation(self, e2e_agent, e2e_ollama_client):
        """E2E: Open Safari and navigate to Google."""
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [
                {"action": "activate_app", "params": {"app_name": "Safari"}},
                {"action": "open_url", "params": {"url": "https://www.google.com", "browser": "Safari"}}
            ],
            "requires_observation": False
        }))

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            result = await e2e_agent.execute("Open Safari and go to Google")

            assert result.success is True
            assert len(result.steps) == 2

    @pytest.mark.asyncio
    async def test_e2e_type_and_submit_search(self, e2e_agent, e2e_ollama_client):
        """E2E: Type search query and press Enter."""
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [
                {"action": "open_url", "params": {"url": "https://www.google.com", "browser": "Safari"}},
                {"action": "type_text", "params": {"text": "weather today"}},
                {"action": "press_key", "params": {"keys": ["return"]}}
            ],
            "requires_observation": False
        }))

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            result = await e2e_agent.execute("Search Google for weather today")

            assert result.success is True
            assert len(result.steps) == 3
            assert result.steps[1].action == "type_text"
            assert result.steps[2].action == "press_key"

    @pytest.mark.asyncio
    async def test_e2e_keyboard_shortcut(self, e2e_agent, e2e_ollama_client):
        """E2E: Execute keyboard shortcut (Cmd+C)."""
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [{"action": "press_key", "params": {"keys": ["command", "c"]}}],
            "requires_observation": False
        }))

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            result = await e2e_agent.execute("Copy the selected text")

            assert result.success is True
            assert result.steps[0].params["keys"] == ["command", "c"]


class TestE2EComplexTaskWorkflows:
    """E2E tests for complex multi-step workflows requiring vision."""

    @pytest.mark.asyncio
    async def test_e2e_youtube_search_and_play_video(self, e2e_agent, e2e_ollama_client):
        """E2E: Search YouTube and play the most popular video."""
        # Initial intent parsing
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [
                {"action": "open_url", "params": {"url": "https://www.youtube.com", "browser": "Safari"}},
                {"action": "type_text", "params": {"text": "cooking tutorials"}},
                {"action": "press_key", "params": {"keys": ["return"]}},
                {"action": "click_element", "params": {"description": "the most viewed video"}}
            ],
            "requires_observation": True
        }))

        # Mock screen observations
        observation_count = [0]
        def vision_side_effect(*args, **kwargs):
            observation_count[0] += 1
            prompt = kwargs.get('prompt', args[1] if len(args) > 1 else '')

            if 'find' in prompt.lower() or 'element' in prompt.lower():
                # Element finding - return bounding box
                return "<box>(300,400,500,450)</box>"
            else:
                # Screen observation
                if observation_count[0] <= 2:
                    return "YouTube homepage is visible with search bar at top"
                return "Search results showing cooking tutorial videos"

        e2e_ollama_client.generate_vision = AsyncMock(side_effect=vision_side_effect)

        # Track LLM planning calls
        planning_calls = [0]
        original_generate = e2e_ollama_client.generate.side_effect

        def generate_side_effect(*args, **kwargs):
            planning_calls[0] += 1
            prompt = kwargs.get('prompt', args[1] if len(args) > 1 else '')

            # First call is intent parsing
            if planning_calls[0] == 1:
                return json.dumps({
                    "steps": [
                        {"action": "open_url", "params": {"url": "https://www.youtube.com", "browser": "Safari"}},
                        {"action": "type_text", "params": {"text": "cooking tutorials"}},
                        {"action": "press_key", "params": {"keys": ["return"]}},
                    ],
                    "requires_observation": True
                })
            # Subsequent calls are planning
            elif planning_calls[0] == 2:
                return json.dumps({
                    "action": "click_element",
                    "params": {"description": "most popular video"},
                    "reasoning": "Need to click the video"
                })
            else:
                return json.dumps({"complete": True, "reasoning": "Video is playing"})

        e2e_ollama_client.generate = AsyncMock(side_effect=generate_side_effect)

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_applescript, \
             patch('automation_agent.orchestrator.agent.ClickAction') as MockClickAction:

            mock_applescript.return_value = AppleScriptResult(success=True)

            mock_click = MagicMock()
            mock_click.execute = AsyncMock(return_value=MagicMock(success=True))
            MockClickAction.return_value = mock_click

            result = await e2e_agent.execute(
                "Search YouTube for cooking tutorials and play the most popular video"
            )

            assert result.success is True
            # Should have executed initial steps + click action
            assert len(result.steps) >= 1

    @pytest.mark.asyncio
    async def test_e2e_find_and_click_login_button(self, e2e_agent, e2e_ollama_client):
        """E2E: Find and click a login button on a webpage."""
        # Setup: Parse as complex task
        call_count = [0]
        def generate_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Intent parsing
                return json.dumps({
                    "steps": [
                        {"action": "open_url", "params": {"url": "https://example.com", "browser": "Safari"}}
                    ],
                    "requires_observation": True
                })
            elif call_count[0] == 2:
                # First planning call - click login
                return json.dumps({
                    "action": "click_element",
                    "params": {"description": "login button"},
                    "reasoning": "Need to click login"
                })
            else:
                # Complete
                return json.dumps({"complete": True, "reasoning": "Login page opened"})

        e2e_ollama_client.generate = AsyncMock(side_effect=generate_side_effect)

        # Vision model for observation and element finding
        def vision_side_effect(*args, **kwargs):
            prompt = kwargs.get('prompt', '')
            if 'find' in prompt.lower():
                return "<box>(800,50,900,80)</box>"
            return "Website loaded with login button in top right corner"

        e2e_ollama_client.generate_vision = AsyncMock(side_effect=vision_side_effect)

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_applescript, \
             patch('automation_agent.orchestrator.agent.ClickAction') as MockClickAction:

            mock_applescript.return_value = AppleScriptResult(success=True)
            mock_click = MagicMock()
            mock_click.execute = AsyncMock(return_value=MagicMock(success=True))
            MockClickAction.return_value = mock_click

            result = await e2e_agent.execute("Go to example.com and click the login button")

            assert result.success is True


class TestE2EErrorHandling:
    """E2E tests for error handling scenarios."""

    @pytest.mark.asyncio
    async def test_e2e_app_not_found(self, e2e_agent, e2e_ollama_client):
        """E2E: Handle application not found error."""
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [{"action": "activate_app", "params": {"app_name": "NonExistentApp"}}],
            "requires_observation": False
        }))

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(
                success=False,
                error="Application NonExistentApp not found"
            )

            result = await e2e_agent.execute("Open NonExistentApp")

            assert result.success is False
            assert "NonExistentApp" in result.steps[0].error

    @pytest.mark.asyncio
    async def test_e2e_element_not_found(self, e2e_agent, e2e_ollama_client):
        """E2E: Handle UI element not found."""
        call_count = [0]
        def generate_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return json.dumps({
                    "steps": [],
                    "requires_observation": True
                })
            elif call_count[0] <= 3:
                return json.dumps({
                    "action": "click_element",
                    "params": {"description": "nonexistent button"},
                    "reasoning": "Try to click"
                })
            else:
                return json.dumps({"complete": True, "reasoning": "Gave up"})

        e2e_ollama_client.generate = AsyncMock(side_effect=generate_side_effect)
        e2e_ollama_client.generate_vision = AsyncMock(return_value="<box>NOT_FOUND</box>")

        result = await e2e_agent.execute("Click the nonexistent button")

        # Should have some failed click attempts
        failed_steps = [s for s in result.steps if not s.success]
        assert len(failed_steps) >= 1

    @pytest.mark.asyncio
    async def test_e2e_partial_failure_recovery(self, e2e_agent, e2e_ollama_client):
        """E2E: Handle partial failure in multi-step workflow."""
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [
                {"action": "activate_app", "params": {"app_name": "Safari"}},
                {"action": "open_url", "params": {"url": "https://invalid-url-that-fails.test"}},
                {"action": "type_text", "params": {"text": "hello"}}
            ],
            "requires_observation": False
        }))

        call_count = [0]
        def run_script_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:  # Second action (open_url) fails
                return AppleScriptResult(success=False, error="URL not reachable")
            return AppleScriptResult(success=True)

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = run_script_side_effect

            result = await e2e_agent.execute("Open Safari, go to invalid URL, and type hello")

            assert result.success is False
            assert len(result.steps) == 2  # Third step not executed
            assert result.steps[0].success is True
            assert result.steps[1].success is False

    @pytest.mark.asyncio
    async def test_e2e_timeout_handling(self, e2e_agent, e2e_ollama_client):
        """E2E: Handle script timeout."""
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [{"action": "activate_app", "params": {"app_name": "Safari"}}],
            "requires_observation": False
        }))

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(
                success=False,
                error="Script timed out after 10s"
            )

            result = await e2e_agent.execute("Open Safari")

            assert result.success is False
            assert "timed out" in result.steps[0].error.lower()


class TestE2EComplexWorkflows:
    """E2E tests for complex real-world workflows."""

    @pytest.mark.asyncio
    async def test_e2e_full_web_search_workflow(self, e2e_agent, e2e_ollama_client):
        """E2E: Complete web search workflow - open browser, search, navigate results."""
        # This simulates a real user workflow
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [
                {"action": "activate_app", "params": {"app_name": "Safari"}},
                {"action": "open_url", "params": {"url": "https://www.google.com", "browser": "Safari"}},
                {"action": "type_text", "params": {"text": "best restaurants near me"}},
                {"action": "press_key", "params": {"keys": ["return"]}}
            ],
            "requires_observation": False
        }))

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            result = await e2e_agent.execute(
                "Open Safari, go to Google, and search for best restaurants near me"
            )

            assert result.success is True
            assert len(result.steps) == 4

            # Verify the workflow sequence
            assert result.steps[0].action == "activate_app"
            assert result.steps[1].action == "open_url"
            assert result.steps[2].action == "type_text"
            assert result.steps[3].action == "press_key"

    @pytest.mark.asyncio
    async def test_e2e_document_editing_workflow(self, e2e_agent, e2e_ollama_client):
        """E2E: Document editing - open app, type, save."""
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [
                {"action": "activate_app", "params": {"app_name": "TextEdit"}},
                {"action": "press_key", "params": {"keys": ["command", "n"]}},  # New doc
                {"action": "type_text", "params": {"text": "Meeting Notes\n\n1. Project update\n2. Next steps"}},
                {"action": "press_key", "params": {"keys": ["command", "s"]}}  # Save
            ],
            "requires_observation": False
        }))

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            result = await e2e_agent.execute(
                "Open TextEdit, create a new document, type meeting notes, and save"
            )

            assert result.success is True
            assert len(result.steps) == 4

    @pytest.mark.asyncio
    async def test_e2e_multi_app_workflow(self, e2e_agent, e2e_ollama_client):
        """E2E: Multi-application workflow - copy from one app, paste to another."""
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [
                {"action": "activate_app", "params": {"app_name": "Safari"}},
                {"action": "press_key", "params": {"keys": ["command", "a"]}},  # Select all
                {"action": "press_key", "params": {"keys": ["command", "c"]}},  # Copy
                {"action": "activate_app", "params": {"app_name": "Notes"}},
                {"action": "press_key", "params": {"keys": ["command", "v"]}}   # Paste
            ],
            "requires_observation": False
        }))

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            result = await e2e_agent.execute(
                "Copy all content from Safari and paste it into Notes"
            )

            assert result.success is True
            assert len(result.steps) == 5

            # Verify app switches
            apps_activated = [s.params.get("app_name") for s in result.steps if s.action == "activate_app"]
            assert "Safari" in apps_activated
            assert "Notes" in apps_activated

    @pytest.mark.asyncio
    async def test_e2e_form_filling_workflow(self, e2e_agent, e2e_ollama_client):
        """E2E: Form filling - navigate fields and enter data."""
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [
                {"action": "type_text", "params": {"text": "John Doe"}},
                {"action": "press_key", "params": {"keys": ["tab"]}},
                {"action": "type_text", "params": {"text": "john@example.com"}},
                {"action": "press_key", "params": {"keys": ["tab"]}},
                {"action": "type_text", "params": {"text": "555-1234"}},
                {"action": "press_key", "params": {"keys": ["tab"]}},
                {"action": "press_key", "params": {"keys": ["return"]}}  # Submit
            ],
            "requires_observation": False
        }))

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            result = await e2e_agent.execute(
                "Fill in the form with name John Doe, email john@example.com, phone 555-1234, then submit"
            )

            assert result.success is True
            assert len(result.steps) == 7

            # Count field navigation (tab presses)
            tab_presses = [s for s in result.steps if s.action == "press_key" and s.params.get("keys") == ["tab"]]
            assert len(tab_presses) == 3


class TestE2EAgentLoopBehavior:
    """E2E tests for agentic loop behavior."""

    @pytest.mark.asyncio
    async def test_e2e_agent_adapts_to_screen_state(self, e2e_agent, e2e_ollama_client):
        """E2E: Agent adapts behavior based on screen observations."""
        iteration = [0]

        def generate_side_effect(*args, **kwargs):
            iteration[0] += 1
            prompt = kwargs.get('prompt', '')

            if iteration[0] == 1:
                # Initial intent parsing
                return json.dumps({
                    "steps": [],
                    "requires_observation": True
                })
            elif "loading" in prompt.lower() or iteration[0] == 2:
                # Agent sees loading state, waits
                return json.dumps({
                    "action": "type_text",
                    "params": {"text": "wait"},
                    "reasoning": "Page is loading"
                })
            else:
                # Page loaded, complete
                return json.dumps({"complete": True, "reasoning": "Page loaded"})

        e2e_ollama_client.generate = AsyncMock(side_effect=generate_side_effect)

        obs_count = [0]
        def vision_side_effect(*args, **kwargs):
            obs_count[0] += 1
            if obs_count[0] == 1:
                return "Page is loading, showing spinner"
            return "Page fully loaded, content visible"

        e2e_ollama_client.generate_vision = AsyncMock(side_effect=vision_side_effect)

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            result = await e2e_agent.execute("Wait for the page to load completely")

            assert result.success is True

    @pytest.mark.asyncio
    async def test_e2e_max_iterations_protection(self, e2e_agent, e2e_ollama_client):
        """E2E: Agent stops at max iterations to prevent infinite loops."""
        # Agent never completes, always wants more actions
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [],
            "requires_observation": True
        }))

        iteration = [0]
        def planning_side_effect(*args, **kwargs):
            iteration[0] += 1
            if iteration[0] == 1:
                return json.dumps({"steps": [], "requires_observation": True})
            return json.dumps({
                "action": "type_text",
                "params": {"text": f"attempt{iteration[0]}"},
                "reasoning": "Keep trying"
            })

        e2e_ollama_client.generate = AsyncMock(side_effect=planning_side_effect)
        e2e_ollama_client.generate_vision = AsyncMock(return_value="Screen unchanged")

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            # Agent configured with max_iterations=10
            result = await e2e_agent.execute("Do something that never completes")

            assert result.success is False
            assert "Max iterations" in result.message
            assert result.iterations == 10


class TestE2EPerformance:
    """E2E tests for performance characteristics."""

    @pytest.mark.asyncio
    async def test_e2e_simple_task_no_vision_calls(self, e2e_agent, e2e_ollama_client):
        """E2E: Simple tasks should not call vision model."""
        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [{"action": "activate_app", "params": {"app_name": "Safari"}}],
            "requires_observation": False
        }))

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new_callable=AsyncMock) as mock_run:
            mock_run.return_value = AppleScriptResult(success=True)

            await e2e_agent.execute("Open Safari")

            # Vision model should NOT be called for simple tasks
            e2e_ollama_client.generate_vision.assert_not_called()

    @pytest.mark.asyncio
    async def test_e2e_multiple_actions_execute_in_sequence(self, e2e_agent, e2e_ollama_client):
        """E2E: Multiple actions execute in correct sequence."""
        execution_order = []

        e2e_ollama_client.generate = AsyncMock(return_value=json.dumps({
            "steps": [
                {"action": "activate_app", "params": {"app_name": "Safari"}},
                {"action": "type_text", "params": {"text": "hello"}},
                {"action": "press_key", "params": {"keys": ["return"]}}
            ],
            "requires_observation": False
        }))

        original_run = AppleScriptResult

        async def tracking_run_script(self, script, timeout=10.0):
            if "activate" in script:
                execution_order.append("activate")
            elif "keystroke" in script and "hello" in script:
                execution_order.append("type")
            elif "key code" in script or "keystroke" in script:
                execution_order.append("key")
            return AppleScriptResult(success=True)

        with patch('automation_agent.actions.applescript.AppleScriptAction._run_script',
                   new=tracking_run_script):

            result = await e2e_agent.execute("Open Safari, type hello, press enter")

            assert result.success is True
            assert execution_order == ["activate", "type", "key"]


class TestE2ECoordinateRangeDetection:
    """Integration-style tests for coordinate range inference."""

    @pytest.mark.asyncio
    async def test_find_element_uses_pixel_space_when_bbox_exceeds_1000(
        self, e2e_ollama_client, e2e_screen_capturer
    ):
        """Coordinates above 1000 should be treated as screenshot pixels."""
        e2e_ollama_client.generate_vision = AsyncMock(return_value="<box>(1200,500,1500,700)</box>")
        observer = ScreenObserver(e2e_ollama_client, e2e_screen_capturer, model="molmo")

        coords = await observer.find_element("reserve button")

        assert coords is not None
        assert coords.x == 1200
        assert coords.y == 500
        assert coords.width == 300
        assert coords.height == 200

    @pytest.mark.asyncio
    async def test_find_element_uses_normalized_space_when_bbox_within_1000(
        self, e2e_ollama_client, e2e_screen_capturer
    ):
        """0-1000 coordinates on large screens should map from normalized space."""
        e2e_ollama_client.generate_vision = AsyncMock(return_value="<box>(500,500,600,600)</box>")
        observer = ScreenObserver(e2e_ollama_client, e2e_screen_capturer, model="molmo")

        coords = await observer.find_element("reserve button")

        assert coords is not None
        assert coords.x == 960
        assert coords.y == 540
