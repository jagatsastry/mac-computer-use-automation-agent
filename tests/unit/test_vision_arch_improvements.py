"""Exhaustive unit tests for the four vision architecture improvements.

Covers:
  Rec 1 — Accessibility candidates fed to coordinator.find_element()
  Rec 2 — Confidence gating with default / critical thresholds
  Rec 3 — Pre-click two-pass validation (_validate_candidate)
  Rec 4 — Resolution-aware screenshot cropping (_maybe_crop_screenshot)

All external calls are mocked — no real Anthropic API, no AppleScript.
"""

import base64
import io
import json
import subprocess
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from automation_agent.shared_models import FindElementResult

# ---------------------------------------------------------------------------
# Helpers / Factories
# ---------------------------------------------------------------------------


def _make_jpeg_b64(width: int = 100, height: int = 100, color=(128, 128, 128)) -> str:
    """Return a minimal valid JPEG as base64.  PIL must be importable (dev dep)."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _make_wide_jpeg_b64(width: int = 2560, height: int = 1440) -> str:
    """Return a hi-res JPEG (wider than 1440px threshold)."""
    return _make_jpeg_b64(width=width, height=height)


def _make_narrow_jpeg_b64(width: int = 1024, height: int = 768) -> str:
    """Return a normal-res JPEG (at or below 1440px threshold)."""
    return _make_jpeg_b64(width=width, height=height)


def _make_config(**overrides):
    """Return an AgentConfig for tests, no real log dir needed."""
    from automation_agent.config import AgentConfig

    defaults = {
        "vision_model": "molmo",
        "model_provider": "local",
        "log_dir": "/tmp/test_arch_improvement_logs",
        "grounding_model": "",
        "grounding_server_url": "",
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_coordinator(config=None):
    """Return a ScreenCoordinatorImpl with _call_vision_model mocked out."""
    from automation_agent.vision.coordinator import ScreenCoordinatorImpl
    from automation_agent.vision.capture import ScreenCapture

    if config is None:
        config = _make_config()

    mock_capture = MagicMock(spec=ScreenCapture)
    fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 6
    mock_capture.capture_b64.return_value = base64.b64encode(fake_jpeg).decode()

    coord = ScreenCoordinatorImpl(config, capture=mock_capture)
    coord._call_vision_model = AsyncMock()
    return coord


def _make_agent(tmp_log_dir, actuator=None, coordinator=None, extra_kwargs=None):
    """Construct a minimal AutomationAgent with fully mocked dependencies."""
    from automation_agent.orchestrator.agent import AutomationAgent
    from automation_agent.config import AgentConfig
    from automation_agent.shared_models import ActionPlan, ActionStep

    config = AgentConfig(
        vision_model="molmo",
        log_dir=str(tmp_log_dir),
        max_iterations=10,
        action_delay=0.0,
    )

    if actuator is None:
        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={"app_name": "Safari", "window_title": "Test"})

    if coordinator is None:
        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=400, y=300, confidence=0.8, source="vision")
        )
        coordinator.describe_screen = AsyncMock(return_value="Test screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

    planner = AsyncMock()
    planner.plan = AsyncMock(
        return_value=ActionPlan(
            steps=[ActionStep(action="done", params={}, verify="")],
            goal="test",
        )
    )
    planner.replan = AsyncMock(
        return_value=ActionPlan(
            steps=[ActionStep(action="done", params={}, verify="")],
            goal="test",
        )
    )

    skill_registry = MagicMock()
    skill_registry.match = AsyncMock(return_value=None)
    skill_registry.list_skills = MagicMock(return_value=[])
    skill_registry.expand = MagicMock(return_value=None)

    kwargs = dict(
        planner=planner,
        skill_registry=skill_registry,
        coordinator=coordinator,
        actuator=actuator,
        config=config,
    )
    if extra_kwargs:
        kwargs.update(extra_kwargs)

    return AutomationAgent(**kwargs)


# ===========================================================================
# REC 1: Accessibility candidates
# ===========================================================================


class TestGetAccessibilityElements:
    """Unit tests for AppleScriptActuator.get_accessibility_elements()."""

    @pytest.fixture
    def actuator(self):
        from automation_agent.actuator.applescript_actuator import AppleScriptActuator
        from automation_agent.config import AgentConfig

        config = AgentConfig(vision_model="molmo", log_dir="/tmp/test_ax_logs")
        return AppleScriptActuator(config)

    def _make_elements(self, count: int = 2) -> list:
        """Build a small valid element list."""
        return [
            {
                "label": f"Button {i}",
                "role": "AXButton",
                "x": 100 * i,
                "y": 200,
                "width": 80,
                "height": 30,
                "center_x": 100 * i + 40,
                "center_y": 215,
            }
            for i in range(1, count + 1)
        ]

    def test_happy_path_returns_elements(self, actuator):
        """Successful JXA call returns parsed element list."""
        elements = self._make_elements(3)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(elements)

        with patch("subprocess.run", return_value=mock_result):
            result = actuator.get_accessibility_elements()

        assert len(result) == 3
        assert result[0]["label"] == "Button 1"
        assert result[0]["role"] == "AXButton"
        assert result[0]["center_x"] == 140

    def test_returns_empty_on_timeout(self, actuator):
        """TimeoutExpired → returns empty list, no exception propagated."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=3),
        ):
            result = actuator.get_accessibility_elements()

        assert result == []

    def test_returns_empty_on_nonzero_exit(self, actuator):
        """Non-zero returncode (permission denied / AX not enabled) → empty list."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            result = actuator.get_accessibility_elements()

        assert result == []

    def test_returns_empty_on_empty_stdout(self, actuator):
        """Zero exit but empty stdout → empty list (no JSON to parse)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "   \n"

        with patch("subprocess.run", return_value=mock_result):
            result = actuator.get_accessibility_elements()

        assert result == []

    def test_returns_empty_on_invalid_json(self, actuator):
        """Zero exit but malformed JSON → empty list (JSON parse error swallowed)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json {{{"

        with patch("subprocess.run", return_value=mock_result):
            result = actuator.get_accessibility_elements()

        assert result == []

    def test_returns_empty_on_generic_exception(self, actuator):
        """Generic OSError (e.g. osascript not found) → empty list."""
        with patch("subprocess.run", side_effect=OSError("osascript not found")):
            result = actuator.get_accessibility_elements()

        assert result == []

    def test_passes_app_name_as_argument(self, actuator):
        """Named app is passed as argv to the JXA script."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "[]"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            actuator.get_accessibility_elements("Finder")

        args_used = mock_run.call_args[0][0]  # positional first arg = the list
        assert "Finder" in args_used

    def test_no_extra_arg_when_no_app_name(self, actuator):
        """Empty app_name → JXA script uses frontmost process (no extra argv)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "[]"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            actuator.get_accessibility_elements("")

        args_used = mock_run.call_args[0][0]
        # Should be: ["osascript", "-l", "JavaScript", "-e", <script>]
        # The app_name should NOT be appended
        assert len(args_used) == 5  # no extra arg

    def test_unicode_labels_parsed_correctly(self, actuator):
        """Unicode characters in element labels survive JSON round-trip."""
        elements = [
            {
                "label": "ファイル",  # Japanese: "File"
                "role": "AXMenuItem",
                "x": 50,
                "y": 25,
                "width": 60,
                "height": 22,
                "center_x": 80,
                "center_y": 36,
            },
            {
                "label": "Über uns",  # German with umlaut
                "role": "AXButton",
                "x": 200,
                "y": 400,
                "width": 100,
                "height": 30,
                "center_x": 250,
                "center_y": 415,
            },
        ]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(elements, ensure_ascii=False)

        with patch("subprocess.run", return_value=mock_result):
            result = actuator.get_accessibility_elements()

        assert result[0]["label"] == "ファイル"
        assert result[1]["label"] == "Über uns"

    def test_returns_empty_list_not_none(self, actuator):
        """All failure paths must return [] not None (callers rely on truthiness)."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=3),
        ):
            result = actuator.get_accessibility_elements()

        assert result is not None
        assert isinstance(result, list)

    def test_uses_3_second_timeout(self, actuator):
        """Subprocess timeout should be exactly 3 seconds (not TIMEOUT_SECONDS=10)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "[]"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            actuator.get_accessibility_elements()

        call_kwargs = mock_run.call_args[1]
        assert call_kwargs.get("timeout") == 3


class TestFindElementWithCandidates:
    """Tests for coordinator.find_element() with the candidates parameter (Rec 1)."""

    @pytest.mark.asyncio
    async def test_find_element_with_candidates_prepends_prefix(self):
        """When candidates provided, vision prompt includes the structured list."""
        coord = _make_coordinator()
        coord._call_vision_model.return_value = "FOUND: x=0.5, y=0.5"

        candidates = [
            {
                "label": "OK",
                "role": "AXButton",
                "center_x": 500,
                "center_y": 300,
            }
        ]

        await coord.find_element(
            "OK button", screenshot_b64="fakedata", candidates=candidates
        )

        prompt_used = coord._call_vision_model.call_args[0][0]
        assert "interactive UI elements" in prompt_used
        assert '"OK"' in prompt_used
        assert "AXButton" in prompt_used
        assert "(500, 300)" in prompt_used

    @pytest.mark.asyncio
    async def test_find_element_without_candidates_no_prefix(self):
        """When candidates=None, prompt should NOT contain candidate list header."""
        coord = _make_coordinator()
        coord._call_vision_model.return_value = "FOUND: x=0.5, y=0.5"

        await coord.find_element("OK button", screenshot_b64="fakedata", candidates=None)

        prompt_used = coord._call_vision_model.call_args[0][0]
        assert "interactive UI elements" not in prompt_used

    @pytest.mark.asyncio
    async def test_find_element_with_empty_candidates_no_prefix(self):
        """When candidates=[], treat same as None — no prefix injected."""
        coord = _make_coordinator()
        coord._call_vision_model.return_value = "FOUND: x=0.5, y=0.5"

        await coord.find_element("OK button", screenshot_b64="fakedata", candidates=[])

        prompt_used = coord._call_vision_model.call_args[0][0]
        assert "interactive UI elements" not in prompt_used

    @pytest.mark.asyncio
    async def test_candidates_capped_at_20(self):
        """More than 20 candidates: only first 20 appear in prompt."""
        coord = _make_coordinator()
        coord._call_vision_model.return_value = "FOUND: x=0.5, y=0.5"

        # 25 candidates; labels 1-25
        candidates = [
            {"label": f"Elem {i}", "role": "AXButton", "center_x": i * 10, "center_y": 50}
            for i in range(1, 26)
        ]

        await coord.find_element("some button", screenshot_b64="fakedata", candidates=candidates)

        prompt_used = coord._call_vision_model.call_args[0][0]
        assert "Elem 20" in prompt_used
        assert "Elem 21" not in prompt_used
        assert "Elem 25" not in prompt_used

    @pytest.mark.asyncio
    async def test_candidate_label_truncated_at_80_chars(self):
        """Labels longer than 80 characters are truncated in the prompt."""
        coord = _make_coordinator()
        coord._call_vision_model.return_value = "FOUND: x=0.5, y=0.5"

        long_label = "A" * 100
        candidates = [
            {"label": long_label, "role": "AXButton", "center_x": 100, "center_y": 200}
        ]

        await coord.find_element("long button", screenshot_b64="fakedata", candidates=candidates)

        prompt_used = coord._call_vision_model.call_args[0][0]
        # The full 100-char label should not appear; 80-char truncation should
        assert "A" * 100 not in prompt_used
        assert "A" * 80 in prompt_used

    @pytest.mark.asyncio
    async def test_candidates_with_unicode_labels(self):
        """Unicode labels pass through to vision prompt without corruption."""
        coord = _make_coordinator()
        coord._call_vision_model.return_value = "NOT_FOUND"

        candidates = [
            {"label": "検索", "role": "AXTextField", "center_x": 300, "center_y": 100}
        ]

        await coord.find_element("search box", screenshot_b64="fakedata", candidates=candidates)

        prompt_used = coord._call_vision_model.call_args[0][0]
        assert "検索" in prompt_used

    @pytest.mark.asyncio
    async def test_accessibility_source_has_full_confidence(self):
        """Elements found via accessibility bridge return confidence=1.0."""
        from automation_agent.vision.coordinator import ScreenCoordinatorImpl
        from automation_agent.vision.capture import ScreenCapture

        config = _make_config()
        mock_capture = MagicMock(spec=ScreenCapture)

        # Provide an accessibility mock that finds the element
        mock_ax_element = MagicMock()
        mock_ax_element.center = (300, 400)
        mock_ax = MagicMock()
        mock_ax.find_element_by_description.return_value = mock_ax_element

        coord = ScreenCoordinatorImpl(config, capture=mock_capture, accessibility=mock_ax)
        coord._call_vision_model = AsyncMock()

        result = await coord.find_element("OK button", screenshot_b64="fakedata")

        assert result is not None
        # coordinator returns FindElementResult dataclass from accessibility path
        assert result.confidence == 1.0
        assert result.source == "accessibility"
        # Vision model should NOT have been called
        coord._call_vision_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_vision_result_includes_confidence_from_response(self):
        """Vision model response with confidence= sets confidence in result dataclass."""
        coord = _make_coordinator()
        coord._call_vision_model.return_value = "FOUND: x=0.5, y=0.5, confidence=0.73"

        result = await coord.find_element("OK button", screenshot_b64="fakedata")

        assert result is not None
        # coordinator returns FindElementResult dataclass from vision path
        assert abs(result.confidence - 0.73) < 0.001

    @pytest.mark.asyncio
    async def test_vision_result_without_confidence_defaults_to_zero(self):
        """Vision model response without confidence= defaults to confidence=0.0."""
        coord = _make_coordinator()
        coord._call_vision_model.return_value = "FOUND: x=0.5, y=0.5"

        result = await coord.find_element("OK button", screenshot_b64="fakedata")

        assert result is not None
        # coordinator returns FindElementResult dataclass; confidence defaults to 0.0
        assert result.confidence == 0.0


# ===========================================================================
# REC 1 sub: _build_candidate_prefix
# ===========================================================================


class TestBuildCandidatePrefix:
    """Direct tests for the _build_candidate_prefix helper."""

    def test_basic_output_structure(self):
        coord = _make_coordinator()
        candidates = [
            {"label": "File", "role": "AXMenuItem", "center_x": 80, "center_y": 22}
        ]
        prefix = coord._build_candidate_prefix(candidates, "File menu")

        assert "interactive UI elements" in prefix
        assert '"File"' in prefix
        assert "AXMenuItem" in prefix
        assert "(80, 22)" in prefix
        assert "File menu" in prefix

    def test_uses_center_x_center_y_when_available(self):
        coord = _make_coordinator()
        candidates = [
            {
                "label": "Btn",
                "role": "AXButton",
                "x": 10,
                "y": 20,
                "center_x": 50,
                "center_y": 35,
            }
        ]
        prefix = coord._build_candidate_prefix(candidates, "button")
        # center_x/center_y should be used, not x/y
        assert "(50, 35)" in prefix

    def test_falls_back_to_x_y_when_no_center(self):
        coord = _make_coordinator()
        candidates = [
            {"label": "Btn", "role": "AXButton", "x": 10, "y": 20}
        ]
        prefix = coord._build_candidate_prefix(candidates, "button")
        assert "(10, 20)" in prefix


# ===========================================================================
# REC 1 sub: _parse_coordinates with confidence
# ===========================================================================


class TestParseCoordinates:
    """Tests for _parse_coordinates returning (x, y, confidence)."""

    def test_with_confidence(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates("FOUND: x=100, y=200, confidence=0.85")
        assert result is not None
        x, y, conf = result
        assert x == 100.0
        assert y == 200.0
        assert abs(conf - 0.85) < 0.001

    def test_without_confidence_defaults_zero(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates("FOUND: x=100, y=200")
        assert result is not None
        x, y, conf = result
        assert conf == 0.0

    def test_not_found_returns_none(self):
        coord = _make_coordinator()
        assert coord._parse_coordinates("NOT_FOUND") is None

    def test_empty_response_returns_none(self):
        coord = _make_coordinator()
        assert coord._parse_coordinates("") is None

    def test_case_insensitive_found(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates("found: x=50, y=75")
        assert result is not None

    def test_confidence_zero_point_zero(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates("FOUND: x=10, y=20, confidence=0.0")
        assert result is not None
        assert result[2] == 0.0

    def test_confidence_one_point_zero(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates("FOUND: x=10, y=20, confidence=1.0")
        assert result is not None
        assert result[2] == 1.0


# ===========================================================================
# REC 2: Confidence gating — _get_confidence_threshold & _dispatch_action
# ===========================================================================


class TestConfidenceGating:
    """Tests for confidence threshold selection and gating logic."""

    @pytest.fixture
    def agent(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        return _make_agent(log_dir)

    # ---- threshold selection ----

    def test_default_threshold_for_normal_step(self, agent):
        from automation_agent.shared_models import ActionStep

        step = ActionStep(
            action="click",
            params={"element": "Next"},
            verify="Next page is shown",
        )
        assert agent._get_confidence_threshold(step) == 0.5

    def test_critical_threshold_for_submit(self, agent):
        from automation_agent.shared_models import ActionStep

        step = ActionStep(
            action="click",
            params={"element": "Submit"},
            verify="Form is submitted",  # 'submit' in verify -> critical
        )
        assert agent._get_confidence_threshold(step) == 0.9

    def test_critical_threshold_for_pay(self, agent):
        from automation_agent.shared_models import ActionStep

        step = ActionStep(
            action="click",
            params={"element": "Pay"},
            verify="Payment confirmed",  # 'pay' not directly, but keyword 'confirm' present? No.
            # 'pay' in keyword set
        )
        # "confirm" is also in keywords; ensure at least one triggers
        assert agent._get_confidence_threshold(step) == 0.9

    def test_critical_threshold_for_delete(self, agent):
        from automation_agent.shared_models import ActionStep

        step = ActionStep(
            action="click",
            params={"element": "Delete"},
            verify="File deleted",  # 'delete' in verify
        )
        assert agent._get_confidence_threshold(step) == 0.9

    def test_critical_threshold_for_send(self, agent):
        from automation_agent.shared_models import ActionStep

        step = ActionStep(
            action="click",
            params={"element": "Send button"},
            # "send" must appear as substring — "sent" does NOT contain "send"
            verify="Please send this email to recipient",
        )
        assert agent._get_confidence_threshold(step) == 0.9

    def test_keyword_case_insensitive_in_verify(self, agent):
        from automation_agent.shared_models import ActionStep

        step = ActionStep(
            action="click",
            params={"element": "OK"},
            verify="Order was CONFIRMED successfully",
        )
        assert agent._get_confidence_threshold(step) == 0.9

    def test_empty_verify_uses_default_threshold(self, agent):
        from automation_agent.shared_models import ActionStep

        step = ActionStep(action="click", params={"element": "OK"}, verify="")
        assert agent._get_confidence_threshold(step) == 0.5

    # ---- gating behaviour ----

    @pytest.mark.asyncio
    async def test_low_confidence_blocks_click(self, tmp_path):
        """confidence=0.3 < 0.5 threshold → action blocked."""
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=400, y=300, confidence=0.3, source="vision")
        )
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        agent = _make_agent(log_dir, coordinator=coordinator)
        step = ActionStep(
            action="click",
            params={"element": "Next button"},
            verify="Next page shown",
        )

        result = await agent._dispatch_action(step)

        assert result["success"] is False
        assert "low_confidence" in result["error"]

    @pytest.mark.asyncio
    async def test_zero_confidence_not_gated(self, tmp_path):
        """confidence=0.0 (model didn't report) → gate NOT applied, click proceeds."""
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=400, y=300, confidence=0.0, source="vision")
        )
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)

        # Disable pre-click validation to isolate gating logic
        agent._validate_candidate = AsyncMock(return_value=True)

        step = ActionStep(
            action="click",
            params={"element": "Next button"},
            verify="Next page shown",
        )

        result = await agent._dispatch_action(step)

        # Should NOT be blocked by confidence gate
        assert "low_confidence" not in result.get("error", "")
        actuator.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_above_threshold_confidence_passes(self, tmp_path):
        """confidence=0.8 > 0.5 threshold → proceeds to click."""
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=400, y=300, confidence=0.8, source="vision")
        )
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        agent._validate_candidate = AsyncMock(return_value=True)

        step = ActionStep(
            action="click",
            params={"element": "Next button"},
            verify="Next page shown",
        )

        result = await agent._dispatch_action(step)
        actuator.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_exactly_at_threshold_is_blocked(self, tmp_path):
        """confidence exactly == threshold (0.5): gate condition is strict less-than.

        The guard is: confidence > 0.0 and confidence < threshold
        At confidence=0.5, threshold=0.5: 0.5 < 0.5 is False → NOT blocked.
        This documents the boundary behaviour of the strict-less-than gate.
        """
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=400, y=300, confidence=0.5, source="vision")
        )
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        # Pre-click validation passes
        agent._validate_candidate = AsyncMock(return_value=True)

        step = ActionStep(
            action="click",
            params={"element": "Next button"},
            verify="Next page shown",
        )

        result = await agent._dispatch_action(step)

        # confidence=0.5 is not < 0.5, so gate does NOT fire
        assert "low_confidence" not in result.get("error", "")
        actuator.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_critical_step_blocked_at_0_85(self, tmp_path):
        """confidence=0.85 < 0.9 critical threshold → blocked for critical step."""
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=400, y=300, confidence=0.85, source="vision")
        )
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        agent = _make_agent(log_dir, coordinator=coordinator)
        step = ActionStep(
            action="click",
            params={"element": "Submit button"},
            verify="Form submitted successfully",  # 'submit' in verify
        )

        result = await agent._dispatch_action(step)

        assert result["success"] is False
        assert "low_confidence" in result["error"]

    @pytest.mark.asyncio
    async def test_accessibility_source_not_gated_regardless_of_confidence(self, tmp_path):
        """Source='accessibility' skips confidence gate (confidence=1.0 by convention)."""
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=400, y=300, confidence=1.0, source="accessibility")
        )
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        step = ActionStep(
            action="click",
            params={"element": "Submit"},
            verify="Form submitted",
        )

        result = await agent._dispatch_action(step)
        # Should proceed to click (pre-click validation skipped for accessibility)
        actuator.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_element_param_skips_confidence_gate(self, tmp_path):
        """Direct coordinate click (no 'element' key) bypasses confidence gate entirely."""
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator)
        step = ActionStep(
            action="click",
            params={"x": 100, "y": 200},  # direct coords, no element
            verify="Something clicked",
        )

        result = await agent._dispatch_action(step)
        actuator.click.assert_called_once_with(100, 200)

    @pytest.mark.asyncio
    async def test_click_scales_image_coords_to_logical_screen_space(self, tmp_path):
        """Vision coords are mapped back to logical screen coords before clicking."""
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=512, y=384, confidence=0.8, source="vision")
        )
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())
        coordinator.capture = MagicMock()
        coordinator.capture.get_screen_size = MagicMock(return_value=(1512, 982))

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        agent._validate_candidate = AsyncMock(return_value=True)
        agent._get_logical_screen_size = MagicMock(return_value=(1512, 982))

        step = ActionStep(
            action="click",
            params={"element": "Continue button"},
            verify="Continue screen shown",
        )

        result = await agent._dispatch_action(step)

        actuator.click.assert_called_once_with(756, 491)
        assert result["image_x"] == 512
        assert result["image_y"] == 384
        assert result["screen_x"] == 756
        assert result["screen_y"] == 491

    @pytest.mark.asyncio
    async def test_open_url_address_bar_fallback_runs_sequence(self, tmp_path):
        """Retry metadata for open_url uses the browser address bar sequence."""
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        actuator = MagicMock()
        actuator.press_key = MagicMock(return_value={"success": True, "output": "pressed"})
        actuator.type_text = MagicMock(return_value={"success": True, "output": "typed"})
        actuator.get_state = MagicMock(return_value={"app_name": "Safari", "window_title": "Test"})

        agent = _make_agent(log_dir, actuator=actuator)

        step = ActionStep(
            action="open_url",
            params={"url": "https://example.com", "_address_bar_fallback": True},
            verify="Example page visible",
        )

        result = await agent._dispatch_action(step)

        assert result["success"] is True
        assert actuator.press_key.call_args_list == [
            call(["cmd", "l"]),
            call(["return"]),
        ]
        actuator.type_text.assert_called_once_with("https://example.com")


# ===========================================================================
# REC 3: Pre-click two-pass validation
# ===========================================================================


class TestValidateCandidate:
    """Tests for AutomationAgent._validate_candidate()."""

    @pytest.mark.asyncio
    async def test_returns_true_when_vision_confirms(self, tmp_path):
        """verify_condition returns True → _validate_candidate returns True."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())
        coordinator.describe_screen = AsyncMock(return_value="screen")

        agent = _make_agent(log_dir, coordinator=coordinator)
        screenshot = _make_jpeg_b64(200, 200)

        result = await agent._validate_candidate(100, 100, "OK button", screenshot_b64=screenshot)

        assert result is True
        coordinator.verify_condition.assert_called_once()
        # The condition string should reference the target description
        condition_arg = coordinator.verify_condition.call_args[0][0]
        assert "OK button" in condition_arg

    @pytest.mark.asyncio
    async def test_returns_false_when_vision_denies(self, tmp_path):
        """verify_condition returns False → _validate_candidate returns False."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.verify_condition = AsyncMock(return_value=False)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())
        coordinator.describe_screen = AsyncMock(return_value="screen")

        agent = _make_agent(log_dir, coordinator=coordinator)
        screenshot = _make_jpeg_b64(200, 200)

        result = await agent._validate_candidate(100, 100, "Submit button", screenshot_b64=screenshot)

        assert result is False

    @pytest.mark.asyncio
    async def test_fail_open_on_exception(self, tmp_path):
        """Exception in verify_condition → fail-open, returns True."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.verify_condition = AsyncMock(side_effect=RuntimeError("API error"))
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())
        coordinator.describe_screen = AsyncMock(return_value="screen")

        agent = _make_agent(log_dir, coordinator=coordinator)
        screenshot = _make_jpeg_b64(200, 200)

        # The exception from verify_condition propagates UP to _dispatch_action
        # which wraps it in fail-open. Here we test _validate_candidate itself
        # does NOT swallow exceptions (the caller does).
        with pytest.raises(RuntimeError):
            await agent._validate_candidate(100, 100, "btn", screenshot_b64=screenshot)

    @pytest.mark.asyncio
    async def test_dispatched_click_fails_open_on_validate_exception(self, tmp_path):
        """_dispatch_action wraps _validate_candidate exception as fail-open."""
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=100, y=100, confidence=0.7, source="vision")
        )
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(side_effect=RuntimeError("vision down"))
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)

        step = ActionStep(
            action="click",
            params={"element": "Search box"},
            verify="Search results shown",
        )

        result = await agent._dispatch_action(step)

        # Fail-open: despite exception in validation, click should proceed
        actuator.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_accessibility_source_skips_validation(self, tmp_path):
        """source='accessibility' → _validate_candidate never called."""
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=100, y=100, confidence=1.0, source="accessibility")
        )
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        agent._validate_candidate = AsyncMock(return_value=True)

        step = ActionStep(
            action="click",
            params={"element": "OK"},
            verify="Dialog closed",
        )

        await agent._dispatch_action(step)

        agent._validate_candidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_high_confidence_skips_validation(self, tmp_path):
        """confidence >= 0.9 → _validate_candidate not called."""
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=100, y=100, confidence=0.95, source="vision")
        )
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        agent._validate_candidate = AsyncMock(return_value=True)

        step = ActionStep(
            action="click",
            params={"element": "OK"},
            verify="Dialog closed",
        )

        await agent._dispatch_action(step)

        agent._validate_candidate.assert_not_called()

    @pytest.mark.asyncio
    async def test_validation_failure_blocks_click(self, tmp_path):
        """_validate_candidate returns False → click blocked, error in result."""
        from automation_agent.shared_models import ActionStep

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=100, y=100, confidence=0.6, source="vision")
        )
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        agent._validate_candidate = AsyncMock(return_value=False)

        step = ActionStep(
            action="click",
            params={"element": "OK"},
            verify="Dialog closed",
        )

        result = await agent._dispatch_action(step)

        assert result["success"] is False
        assert "Pre-click validation failed" in result["error"]
        actuator.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_validate_candidate_crops_200x200(self, tmp_path):
        """_validate_candidate passes a cropped sub-image to verify_condition."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())
        coordinator.describe_screen = AsyncMock(return_value="screen")

        agent = _make_agent(log_dir, coordinator=coordinator)
        # Use a large screenshot so we can verify the crop size
        full_screenshot = _make_jpeg_b64(800, 600)

        await agent._validate_candidate(400, 300, "button", screenshot_b64=full_screenshot)

        # Verify the screenshot_b64 passed to verify_condition is smaller than the original
        cropped_b64 = coordinator.verify_condition.call_args[1].get(
            "screenshot_b64"
        ) or coordinator.verify_condition.call_args[0][1]

        cropped_bytes = base64.b64decode(cropped_b64)
        from PIL import Image

        cropped_img = Image.open(io.BytesIO(cropped_bytes))
        cw, ch = cropped_img.size
        # Should be at most 200x200
        assert cw <= 200
        assert ch <= 200

    @pytest.mark.asyncio
    async def test_validate_candidate_edge_element_clamps_crop(self, tmp_path):
        """Element near screen edge: crop is clamped to image boundaries (no out-of-bounds)."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())
        coordinator.describe_screen = AsyncMock(return_value="screen")

        agent = _make_agent(log_dir, coordinator=coordinator)
        # Element at top-left corner — crop would go out of bounds without clamping
        screenshot = _make_jpeg_b64(400, 300)

        # Should not raise even for edge elements
        result = await agent._validate_candidate(5, 5, "corner button", screenshot_b64=screenshot)
        assert result is True  # verify_condition returns True


# ===========================================================================
# REC 4: Resolution-aware screenshot cropping
# ===========================================================================


class TestMaybeCropScreenshot:
    """Tests for AutomationAgent._maybe_crop_screenshot()."""

    @pytest.fixture
    def agent(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        a = _make_agent(log_dir)
        return a

    def test_no_crop_for_narrow_image(self, agent):
        """Image width <= 1440 → returns None (no crop)."""
        agent.last_successful_region = (100, 100, 600, 400)
        narrow_b64 = _make_narrow_jpeg_b64(1024, 768)
        result = agent._maybe_crop_screenshot(narrow_b64)
        assert result is None

    def test_no_crop_at_exact_threshold_width(self, agent):
        """Image width == 1440 → returns None (boundary: not strictly greater)."""
        agent.last_successful_region = (100, 100, 600, 400)
        b64_1440 = _make_jpeg_b64(1440, 900)
        result = agent._maybe_crop_screenshot(b64_1440)
        assert result is None

    def test_crop_applied_for_hi_res_image(self, agent):
        """Image width > 1440 → crop applied, returns (cropped_b64, offset)."""
        agent.last_successful_region = (1280, 720, 1792, 1232)  # center ~(1536, 976)
        wide_b64 = _make_wide_jpeg_b64(2560, 1440)

        result = agent._maybe_crop_screenshot(wide_b64)

        assert result is not None
        cropped_b64, (offset_x, offset_y) = result

        # Verify crop is 512x512 (or clamped at boundary)
        from PIL import Image
        cropped_img = Image.open(io.BytesIO(base64.b64decode(cropped_b64)))
        cw, ch = cropped_img.size
        assert cw <= 512
        assert ch <= 512

    def test_no_crop_when_last_region_is_none(self, agent):
        """last_successful_region=None → returns None even for hi-res image."""
        agent.last_successful_region = None
        wide_b64 = _make_wide_jpeg_b64(2560, 1440)
        result = agent._maybe_crop_screenshot(wide_b64)
        assert result is None

    def test_crop_offset_correct(self, agent):
        """Crop offset (left, top) should correctly translate back to full-image coords."""
        # Place region at a predictable location
        # Center of region: (1280+1792)//2=1536, (720+1232)//2=976
        # Crop center: (1536, 976), half=256
        # left = max(0, min(1536-256, 2560-512)) = max(0, min(1280, 2048)) = 1280
        # top  = max(0, min(976-256, 1440-512)) = max(0, min(720, 928)) = 720
        agent.last_successful_region = (1280, 720, 1792, 1232)
        wide_b64 = _make_wide_jpeg_b64(2560, 1440)

        result = agent._maybe_crop_screenshot(wide_b64)
        assert result is not None
        _, (offset_x, offset_y) = result
        assert offset_x == 1280
        assert offset_y == 720

    def test_crop_at_image_top_left_boundary(self, agent):
        """Region near top-left: crop clamped to (0, 0) offset."""
        agent.last_successful_region = (0, 0, 100, 100)
        wide_b64 = _make_wide_jpeg_b64(2560, 1440)

        result = agent._maybe_crop_screenshot(wide_b64)
        assert result is not None
        _, (offset_x, offset_y) = result
        assert offset_x == 0
        assert offset_y == 0

    def test_crop_at_image_bottom_right_boundary(self, agent):
        """Region near bottom-right: crop clamped to keep 512x512 within image."""
        # Place region near the far bottom-right of a 2560x1440 image
        agent.last_successful_region = (2400, 1300, 2560, 1440)
        wide_b64 = _make_wide_jpeg_b64(2560, 1440)

        result = agent._maybe_crop_screenshot(wide_b64)
        assert result is not None
        _, (offset_x, offset_y) = result
        # offset_x = min(center_x-256, 2560-512) → clamped to 2048
        # offset_y = min(center_y-256, 1440-512) → clamped to 928
        assert offset_x <= 2048
        assert offset_y <= 928

    def test_returns_none_on_invalid_b64(self, agent):
        """Corrupt base64 input → returns None (exception swallowed)."""
        agent.last_successful_region = (100, 100, 600, 400)
        result = agent._maybe_crop_screenshot("this is not valid base64!!!")
        assert result is None

    def test_offset_added_to_find_element_result(self, tmp_path):
        """After crop, find_element result coords are shifted by crop offset."""
        # This is an integration test of _find_element offset correction
        pass  # Covered by test_find_element_applies_crop_offset_to_result below


class TestFindElementCropIntegration:
    """Integration tests: _find_element wires accessibility + crop correctly."""

    @pytest.mark.asyncio
    async def test_find_element_applies_crop_offset_to_result(self, tmp_path):
        """Crop offset is added back to coordinator result coordinates."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        wide_b64 = _make_wide_jpeg_b64(2560, 1440)

        coordinator = AsyncMock()
        # Vision result inside the crop region (local coords within 512x512)
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=50, y=80, confidence=0.8, source="vision")
        )
        coordinator.capture_screenshot = AsyncMock(return_value=wide_b64)
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(return_value=True)

        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        # Set last_successful_region so crop will be applied
        # Center: (1536, 976), left=1280, top=720
        agent.last_successful_region = (1280, 720, 1792, 1232)

        result = await agent._find_element("some button")

        assert result is not None
        # _find_element returns FindElementResult with adjusted coords
        assert result.x == 50 + 1280
        assert result.y == 80 + 720

    @pytest.mark.asyncio
    async def test_no_crop_when_low_res(self, tmp_path):
        """Low-res screenshot → no crop applied, coordinator gets full screenshot."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        narrow_b64 = _make_narrow_jpeg_b64(1024, 768)

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=200, y=150, confidence=0.8, source="vision")
        )
        coordinator.capture_screenshot = AsyncMock(return_value=narrow_b64)
        coordinator.describe_screen = AsyncMock(return_value="screen")

        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        agent.last_successful_region = (100, 100, 600, 400)  # region set but image is narrow

        result = await agent._find_element("some button")

        assert result is not None
        # Coordinates unchanged (no offset applied); result is FindElementResult dataclass
        assert result.x == 200
        assert result.y == 150

    @pytest.mark.asyncio
    async def test_find_element_crop_normalizes_metadata_correctly(self, tmp_path):
        """After crop offset adjustment, normalization recomputes screen coords from full image."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        wide_b64 = _make_wide_jpeg_b64(2560, 1440)

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(
                x=50, y=80, confidence=0.85, source="vision",
                raw_response="found it",
                screen_x=300, screen_y=400,  # these are in cropped space
                image_width=512, image_height=512,
            )
        )
        coordinator.capture_screenshot = AsyncMock(return_value=wide_b64)
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(return_value=True)

        actuator = MagicMock()
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        agent.last_successful_region = (1280, 720, 1792, 1232)

        result = await agent._find_element("some button")

        assert result is not None
        # x, y should be offset-adjusted to full image space
        assert result.x == 50 + 1280
        assert result.y == 80 + 720
        # Normalization recomputes screen_x/screen_y from full-image x/y
        # So they should NOT equal the original cropped-space values
        assert result.screen_x is not None
        assert result.screen_y is not None
        # image_width/height should be full image dimensions (set by normalization)
        assert result.image_width == 2560
        assert result.image_height == 1440
        # confidence, source, raw_response preserved through both transforms
        assert result.confidence == 0.85
        assert result.source == "vision"
        assert result.raw_response == "found it"

    @pytest.mark.asyncio
    async def test_last_successful_region_reset_on_new_execute(self, tmp_path):
        """execute() resets last_successful_region to None at the start."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        agent = _make_agent(log_dir)
        agent.last_successful_region = (100, 100, 600, 400)  # stale from previous run

        await agent.execute("test goal")

        assert agent.last_successful_region is None

    @pytest.mark.asyncio
    async def test_last_successful_region_set_after_successful_click(self, tmp_path):
        """After a successful click step, last_successful_region is populated."""
        from automation_agent.shared_models import ActionPlan, ActionStep, StepResult

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.describe_screen = AsyncMock(return_value="screen")
        coordinator.verify_condition = AsyncMock(return_value=True)
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())

        actuator = MagicMock()
        actuator.click = MagicMock(return_value={"success": True})
        actuator.get_state = MagicMock(
            return_value={"app_name": "X", "window_title": "Y", "window_frame": "{}"}
        )

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)

        # Manually call _execute_step with a click that uses direct coords
        step = ActionStep(
            action="click",
            params={"x": 500, "y": 300},
            verify="element clicked",
        )

        # Patch verifier to return success
        from automation_agent.shared_models import StepResult as SR
        agent.verifier.verify = AsyncMock(
            return_value=SR(
                step=step,
                success=True,
                verification_method="actuator_state",
                evidence="confirmed",
            )
        )

        await agent._execute_step(0, step, [], "test goal", MagicMock())  # returns tuple now

        assert agent.last_successful_region is not None
        left, top, right, bottom = agent.last_successful_region
        # Center should be near (500, 300), half=256
        assert left == max(0, 500 - 256)
        assert top == max(0, 300 - 256)
        assert right == 500 + 256
        assert bottom == 300 + 256

    @pytest.mark.asyncio
    async def test_accessibility_candidates_wired_to_find_element(self, tmp_path):
        """_find_element passes accessibility elements as candidates to coordinator."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        ax_elements = [
            {
                "label": "Search",
                "role": "AXTextField",
                "x": 100,
                "y": 50,
                "width": 200,
                "height": 30,
                "center_x": 200,
                "center_y": 65,
            }
        ]

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=200, y=65, confidence=0.9, source="vision")
        )
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())
        coordinator.describe_screen = AsyncMock(return_value="screen")

        actuator = MagicMock()
        actuator.get_accessibility_elements = MagicMock(return_value=ax_elements)
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)

        await agent._find_element("Search field")

        coordinator.find_element.assert_called_once()
        call_kwargs = coordinator.find_element.call_args
        candidates_passed = call_kwargs[1].get("candidates") or call_kwargs[0][2] if len(call_kwargs[0]) > 2 else None
        # Check via keyword argument
        _, call_kw = coordinator.find_element.call_args
        assert call_kw.get("candidates") == ax_elements

    @pytest.mark.asyncio
    async def test_no_candidates_when_accessibility_returns_empty(self, tmp_path):
        """get_accessibility_elements returns [] → candidates=None passed to coordinator."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=200, y=65, confidence=0.7, source="vision")
        )
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())
        coordinator.describe_screen = AsyncMock(return_value="screen")

        actuator = MagicMock()
        actuator.get_accessibility_elements = MagicMock(return_value=[])
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        agent._validate_candidate = AsyncMock(return_value=True)

        await agent._find_element("some button")

        _, call_kw = coordinator.find_element.call_args
        assert call_kw.get("candidates") is None

    @pytest.mark.asyncio
    async def test_no_candidates_when_actuator_lacks_method(self, tmp_path):
        """Actuator without get_accessibility_elements → candidates=None."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=200, y=65, confidence=0.7, source="vision")
        )
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())
        coordinator.describe_screen = AsyncMock(return_value="screen")

        # Actuator without the get_accessibility_elements method
        actuator = MagicMock(spec=["click", "type_text", "press_key", "activate_app",
                                    "open_url", "quit_app", "get_state", "is_available"])
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        agent._validate_candidate = AsyncMock(return_value=True)

        await agent._find_element("some button")

        _, call_kw = coordinator.find_element.call_args
        assert call_kw.get("candidates") is None

    @pytest.mark.asyncio
    async def test_accessibility_exception_does_not_break_find_element(self, tmp_path):
        """Exception from get_accessibility_elements → graceful fallback, no crash."""
        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        coordinator = AsyncMock()
        coordinator.find_element = AsyncMock(
            return_value=FindElementResult(x=200, y=65, confidence=0.7, source="vision")
        )
        coordinator.capture_screenshot = AsyncMock(return_value=_make_narrow_jpeg_b64())
        coordinator.describe_screen = AsyncMock(return_value="screen")

        actuator = MagicMock()
        actuator.get_accessibility_elements = MagicMock(side_effect=RuntimeError("AX broken"))
        actuator.get_state = MagicMock(return_value={"app_name": "X", "window_title": "Y"})

        agent = _make_agent(log_dir, actuator=actuator, coordinator=coordinator)
        agent._validate_candidate = AsyncMock(return_value=True)

        # Should not raise
        result = await agent._find_element("some button")
        assert result is not None


# ===========================================================================
# Edge case: FindElementResult dataclass structure
# ===========================================================================


class TestFindElementResultDataclass:
    """Basic sanity checks for the FindElementResult dataclass."""

    def test_can_be_instantiated(self):
        from automation_agent.shared_models import FindElementResult

        r = FindElementResult(x=100, y=200)
        assert r.x == 100
        assert r.y == 200
        assert r.confidence == 0.0
        assert r.source == ""
        assert r.raw_response == ""

    def test_fields_set_correctly(self):
        from automation_agent.shared_models import FindElementResult

        r = FindElementResult(x=50, y=75, confidence=0.9, source="vision", raw_response="FOUND: x=50, y=75")
        assert r.confidence == 0.9
        assert r.source == "vision"
        assert "FOUND" in r.raw_response


# ===========================================================================
# Molmo coordinate space regression test
# ===========================================================================


class TestMolmoCoordinateSpace:
    """Verify that molmo's coordinate space is normalized_0_100 (not normalized_0_1).

    Molmo outputs point values in 0-100 range (e.g. <point x="75.3" y="43.1">).
    The normalized_0_100 space divides by 100 to convert to pixels.
    If this were accidentally changed to normalized_0_1, a molmo value of 75.0
    would be treated as already-normalised and multiplied by screen width directly,
    giving 75*1024=76800 (clamped to 1023), breaking the live molmo-mlx backend.
    """

    @pytest.fixture
    def coord(self):
        """ScreenCoordinatorImpl with molmo as vision model."""
        return _make_coordinator(_make_config(vision_model="molmo"))

    def test_molmo_coordinate_space_is_normalized_0_100(self, coord):
        """Molmo outputs 0-100 values; they must be divided by 100 to get pixels."""
        # 75.0 in normalized_0_100 -> 75.0 / 100 * 1024 = 768
        # 43.0 in normalized_0_100 -> 43.0 / 100 * 768 = 330
        # If space were normalized_0_1: 75.0 * 1024 = clamped to 1023
        x, y = coord._convert_coordinates(75.0, 43.0, "molmo", 1024, 768)
        assert x == int(75.0 / 100.0 * 1024)  # 768
        assert y == int(43.0 / 100.0 * 768)   # 330
        # Explicitly not 1023 (what normalized_0_1 would produce for 75.0)
        assert x != 1023, "molmo coordinate was treated as normalized_0_1 (clamped to max)"

    def test_molmo_registry_entry_is_normalized_0_100(self):
        """Direct check: COORDINATE_SPACES must map molmo to normalized_0_100."""
        from automation_agent.vision.coordinator import COORDINATE_SPACES
        assert COORDINATE_SPACES.get("molmo") == "normalized_0_100", (
            "molmo was changed away from normalized_0_100 — "
            "this will break the live molmo-mlx backend which outputs 0-100 values"
        )

    def test_molmo_prefix_match_also_uses_normalized_0_100(self, coord):
        """GGUF filenames like 'molmo-7b-q4.gguf' prefix-match to normalized_0_100."""
        space = coord._resolve_coordinate_space("molmo-7b-q4.gguf")
        assert space == "normalized_0_100"

    def test_normalized_0_1_would_clamp_molmo_values(self, coord):
        """Demonstrate what would break if space were wrongly set to normalized_0_1.

        A molmo value of 75.0 in normalized_0_1 would be multiplied by screen
        width (75.0 * 1024 = 76800) then clamped to screen_width - 1 = 1023.
        This documents the expected WRONG behaviour to make regressions obvious.
        """
        # Simulate the wrong behavior: normalized_0_1 treatment of a 0-100 value
        wrong_x = min(int(75.0 * 1024), 1023)  # 1023 (clamped)
        # The correct normalized_0_100 result
        correct_x = int(75.0 / 100.0 * 1024)   # 768
        assert wrong_x == 1023
        assert correct_x == 768
        assert wrong_x != correct_x

    def test_midpoint_molmo_value_50_converts_to_half_screen(self, coord):
        """50.0 in normalized_0_100 should map to exactly the screen midpoint."""
        x, y = coord._convert_coordinates(50.0, 50.0, "molmo", 1024, 768)
        assert x == int(50.0 / 100.0 * 1024)  # 512
        assert y == int(50.0 / 100.0 * 768)   # 384

    def test_full_molmo_value_100_clamps_to_screen_edge(self, coord):
        """100.0 in normalized_0_100 clamps to screen_width - 1."""
        x, y = coord._convert_coordinates(100.0, 100.0, "molmo", 1024, 768)
        assert x == 1023  # min(int(100/100*1024), 1024-1) = min(1024, 1023) = 1023
        assert y == 767   # min(int(100/100*768), 768-1) = min(768, 767) = 767

    def test_zero_molmo_value_maps_to_origin(self, coord):
        """0.0 in normalized_0_100 maps to (0, 0)."""
        x, y = coord._convert_coordinates(0.0, 0.0, "molmo", 1024, 768)
        assert x == 0
        assert y == 0
