"""Tests for grounding coordinate pipeline fixes.

Bug: Molmo v1 3-bit sometimes returns <points x1="..." y1="..."> format
that the parser didn't handle, AND returns coordinates outside the declared
0-100 range (e.g., y=250). Both issues caused clicks to land on the Dock
(y=767) instead of the target element.

Fixes:
1. _parse_coordinates handles <points x1/y1 x2/y2> bounding box format
2. _convert_coordinates auto-escalates coordinate space when values exceed range
3. find_element.md prompt no longer shows large-number examples that prime
   the model to output in the wrong coordinate space
"""

import pytest

from automation_agent.config import AgentConfig
from automation_agent.vision.coordinator import ScreenCoordinatorImpl


def _make_coordinator():
    config = AgentConfig(
        vision_server_url="http://localhost:8091",
        vision_model="molmo",
        grounding_model="",
        grounding_server_url="",
        model_provider="local",
    )
    return ScreenCoordinatorImpl(config)


class TestParseCoordinatesPointsXY:
    """Tests for <points x1/y1 x2/y2> bounding box format parsing."""

    def test_points_bbox_full(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates(
            '<points x1="14.1" y1="250" x2="14.1" y2="251" '
            'alt="Sort by dropdown">Sort by dropdown</points>'
        )
        assert result is not None
        x, y, conf = result
        assert abs(x - 14.1) < 0.01
        assert abs(y - 250.5) < 0.01  # center of 250-251
        assert conf == 0.0

    def test_points_bbox_with_markdown_wrapper(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates(
            ' - `<points x1="14.1" y1="250" x2="14.1" y2="251" '
            'alt="Sort by dropdown">Sort by dropdown</points>'
        )
        assert result is not None
        x, y, _ = result
        assert abs(x - 14.1) < 0.01
        assert abs(y - 250.5) < 0.01

    def test_points_bbox_no_x2_y2(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates(
            '<points x1="50.0" y1="32.0" alt="Button">Button</points>'
        )
        assert result is not None
        x, y, _ = result
        assert abs(x - 50.0) < 0.01
        assert abs(y - 32.0) < 0.01

    def test_points_bbox_wide_spread(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates(
            '<points x1="10" y1="200" x2="30" y2="300" alt="Region">Region</points>'
        )
        assert result is not None
        x, y, _ = result
        assert abs(x - 20.0) < 0.01  # center of 10-30
        assert abs(y - 250.0) < 0.01  # center of 200-300

    def test_points_self_closing(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates(
            '<points x1="50" y1="50" x2="60" y2="60" />'
        )
        assert result is not None
        x, y, _ = result
        assert abs(x - 55.0) < 0.01
        assert abs(y - 55.0) < 0.01


class TestConvertCoordinatesOutOfRange:
    """Tests for out-of-range coordinate detection and auto-escalation."""

    def test_normal_0_100_in_range(self):
        coord = _make_coordinator()
        x, y = coord._convert_coordinates(50.0, 50.0, "molmo", 1024, 768)
        assert x == 512
        assert y == 384

    def test_0_100_at_boundary(self):
        coord = _make_coordinator()
        x, y = coord._convert_coordinates(100.0, 100.0, "molmo", 1024, 768)
        assert x == 1023  # clamped to w-1
        assert y == 767  # clamped to h-1

    def test_out_of_range_y_escalates_to_1000(self):
        """The critical bug: y=250 in 0-100 space → 1920 → clamped to 767 (Dock).
        With fix: auto-escalates to 0-1000 → y=192 (correct Sort button position).
        """
        coord = _make_coordinator()
        x, y = coord._convert_coordinates(14.1, 250.0, "molmo", 1024, 768)
        # In 0-1000 space: x=14.1/1000*1024=14, y=250/1000*768=192
        assert x == 14
        assert y == 192

    def test_out_of_range_both_escalates(self):
        coord = _make_coordinator()
        x, y = coord._convert_coordinates(141.0, 250.0, "molmo", 1024, 768)
        # In 0-1000: x=141/1000*1024=144, y=250/1000*768=192
        assert x == 144
        assert y == 192

    def test_out_of_range_x_only_escalates(self):
        coord = _make_coordinator()
        x, y = coord._convert_coordinates(150.0, 50.0, "molmo", 1024, 768)
        # In 0-1000: x=150/1000*1024=153, y=50/1000*768=38
        assert x == 153
        assert y == 38

    def test_1000_space_not_affected(self):
        """Molmo2 uses 0-1000 natively — no escalation needed."""
        coord = _make_coordinator()
        x, y = coord._convert_coordinates(500.0, 500.0, "molmo2", 1024, 768)
        assert x == 512
        assert y == 384

    def test_out_of_range_0_1_escalates_to_100(self):
        coord = _make_coordinator()
        config = AgentConfig(
            vision_server_url="http://localhost:8091",
            vision_model="molmo",
            grounding_model="",
            grounding_server_url="",
            model_provider="local",
        )
        # Fake a model registered as 0-1 space
        import automation_agent.vision.coordinator as cmod
        original = cmod.COORDINATE_SPACES.copy()
        cmod.COORDINATE_SPACES["test_01_model"] = "normalized_0_1"
        try:
            coord2 = ScreenCoordinatorImpl(config)
            x, y = coord2._convert_coordinates(50.0, 32.0, "test_01_model", 1024, 768)
            # Escalated to 0-100: x=50/100*1024=512, y=32/100*768=245
            assert x == 512
            assert y == 245
        finally:
            cmod.COORDINATE_SPACES.clear()
            cmod.COORDINATE_SPACES.update(original)


class TestParseCoordinatesExistingFormats:
    """Regression tests — existing formats still work."""

    def test_found_comma_separated(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates("FOUND: x=50.5, y=32.1, confidence=0.9")
        assert result == pytest.approx((50.5, 32.1, 0.9), abs=0.01)

    def test_found_space_separated_with_y_label(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates("FOUND: x=14.1 y=25.0")
        assert result is not None
        assert abs(result[0] - 14.1) < 0.01
        assert abs(result[1] - 25.0) < 0.01

    def test_found_space_separated_no_y_label(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates("FOUND: x=14.1 250")
        assert result is not None
        assert abs(result[0] - 14.1) < 0.01
        assert abs(result[1] - 250.0) < 0.01

    def test_not_found(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates("NOT_FOUND")
        assert result is None

    def test_point_tag(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates('<point x="50" y="32" />')
        assert result is not None
        assert abs(result[0] - 50.0) < 0.01
        assert abs(result[1] - 32.0) < 0.01

    def test_points_coords_format(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates('<points coords="0 500 250" />')
        assert result is not None
        assert abs(result[0] - 500.0) < 0.01
        assert abs(result[1] - 250.0) < 0.01

    def test_json_format(self):
        coord = _make_coordinator()
        result = coord._parse_coordinates('{"x": 50.5, "y": 32.1, "confidence": 0.85}')
        assert result is not None
        assert abs(result[0] - 50.5) < 0.01
        assert abs(result[1] - 32.1) < 0.01
        assert abs(result[2] - 0.85) < 0.01


class TestEndToEndGroundingBug:
    """End-to-end test reproducing the exact bug from customer testing.

    Molmo returned: <points x1="14.1" y1="250" x2="14.1" y2="251" alt="Sort by dropdown">
    This was unparsed → fell back → second call returned FOUND: x=14.1, y=250 →
    converted via 0-100 space → y=1920 → clamped to 767 (the Dock).
    """

    def test_points_bbox_now_parsed(self):
        """The <points x1/y1> format is now parsed correctly."""
        coord = _make_coordinator()
        response = (
            ' - `<points x1="14.1" y1="250" x2="14.1" y2="251" '
            'alt="Sort by dropdown">Sort by dropdown</points>'
        )
        result = coord._parse_coordinates(response)
        assert result is not None, "Parser must handle <points x1/y1> format"
        x, y, _ = result
        assert abs(x - 14.1) < 0.01
        assert abs(y - 250.5) < 0.01

    def test_out_of_range_coordinates_not_clamped_to_dock(self):
        """y=250 in 0-100 space used to give y=1920→767 (Dock). Now gives 192."""
        coord = _make_coordinator()
        x, y = coord._convert_coordinates(14.1, 250.5, "molmo", 1024, 768)
        # In auto-escalated 0-1000 space:
        # x=14.1/1000*1024=14, y=250.5/1000*768=192
        assert y < 400, f"y={y} should NOT be at the Dock (767)"
        assert y == 192

    def test_full_pipeline_sort_button(self):
        """Full parse+convert pipeline for the exact Sort button case."""
        coord = _make_coordinator()
        response = (
            '<points x1="14.1" y1="250" x2="14.1" y2="251" '
            'alt="Sort by dropdown">Sort by dropdown</points>'
        )
        parsed = coord._parse_coordinates(response)
        assert parsed is not None
        raw_x, raw_y, conf = parsed
        x, y = coord._convert_coordinates(raw_x, raw_y, "molmo", 1024, 768)
        # Correct: Sort button at roughly x=14, y=192 (not x=144, y=767)
        assert x == 14
        assert y == 192

    def test_found_format_out_of_range_also_fixed(self):
        """FOUND: x=14.1, y=250 — same coordinates in text format."""
        coord = _make_coordinator()
        parsed = coord._parse_coordinates("FOUND: x=14.1, y=250.0")
        assert parsed is not None
        x, y = coord._convert_coordinates(parsed[0], parsed[1], "molmo", 1024, 768)
        assert y < 400, f"y={y} should NOT be at the Dock (767)"

    def test_in_range_coordinates_still_use_0_100(self):
        """Coordinates within 0-100 range should NOT be escalated."""
        coord = _make_coordinator()
        # Price: Low to High was found at raw coords ~(1.07, 41.0) in 0-100
        x, y = coord._convert_coordinates(1.07, 41.0, "molmo", 1024, 768)
        # 0-100 space: x=1.07/100*1024=10, y=41/100*768=314
        assert x == 10
        assert y == 314


class TestPromptNoLargeNumbers:
    """Verify the find_element.md prompt doesn't contain large-number examples."""

    def test_prompt_no_500_250_examples(self):
        """The old prompt had <point x='500' y='250'> which primed Molmo to
        output in 0-500+ range instead of 0-100."""
        from pathlib import Path
        prompt_path = (
            Path(__file__).resolve().parent.parent.parent
            / "src" / "automation_agent" / "vision" / "prompts" / "find_element.md"
        )
        content = prompt_path.read_text()
        assert "<point" not in content, "Prompt should not contain <point> examples"
        assert "<points" not in content, "Prompt should not contain <points> examples"
        assert "500" not in content, "Prompt should not contain large number examples"
        assert "FOUND:" in content, "Prompt must still ask for FOUND: format"
        assert "NOT_FOUND" in content, "Prompt must still accept NOT_FOUND"
