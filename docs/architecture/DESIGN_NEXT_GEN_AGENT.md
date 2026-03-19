# Design Document: Next-Generation macOS Automation Agent

*Transforming our agent with learnings from VyUI, Agent S2, and the open-source ecosystem*

---

## 1. Objectives

Transform the current `macos-automation-agent` from a single-model, vision-only, stateless agent into a **multi-expert, accessibility-aware, context-persistent** agent that approaches the reliability levels demonstrated by VyUI (~92% grounding accuracy) and Agent S2 (#1 on OSWorld).

### Target Metrics

| Metric | Current | Target | How |
|--------|---------|--------|-----|
| UI element grounding accuracy | ~40-60% | ~85% | UGround + accessibility hybrid |
| Action success rate (per-click) | ~50-70% | ~90% | Verification + retry |
| End-to-end task completion | ~30-40% | ~70% | All improvements combined |
| Observation latency | 3-7s | 1-2s | Accessibility fast-path |
| Max iterations needed | 35 | 15 | Better grounding = fewer retries |

---

## 2. Architecture Overview

### Current Architecture

```
User Command → IntentParser → Sequential or Agentic Loop
                                       │
                                ┌──────┴──────┐
                                │  1 VLM does  │
                                │  everything  │
                                └──────┬──────┘
                                       │
                          Screenshot → Qwen2-VL → Coordinates → Click
                          (one model, one strategy, no verification)
```

### Proposed Architecture

```
User Command → IntentParser → Task Router
                                  │
                    ┌─────────────┼─────────────┐
                    │             │              │
              ┌─────▼─────┐ ┌────▼────┐ ┌──────▼──────┐
              │ Sequential │ │Agentic  │ │  Domain     │
              │ Executor   │ │  Loop   │ │  Workflow   │
              └───────────┘ │(enhanced)│ │  (pluggable)│
                            └────┬────┘ └─────────────┘
                                 │
                    ┌────────────┼────────────┐
                    │            │             │
              ┌─────▼─────┐ ┌───▼───┐ ┌──────▼──────┐
              │  Context   │ │Mixture│ │  Action     │
              │  Monitor   │ │  of   │ │  Verifier   │
              │(persistent)│ │Ground.│ │(screenshot  │
              └────────────┘ │(MoG)  │ │    diff)    │
                             └───┬───┘ └─────────────┘
                     ┌───────────┼───────────┐
                     │           │            │
               ┌─────▼─────┐ ┌──▼──┐ ┌──────▼──────┐
               │Accessibility│ │Vision│ │    OCR     │
               │  Expert    │ │Expert│ │   Expert   │
               │ (AXUIElem) │ │(UGr.)│ │ (Tesseract)│
               └────────────┘ └─────┘ └────────────┘
```

---

## 3. Phase 1: Accessibility Bridge (Week 1)

### 3.1 Problem

We're currently **vision-only** — every element lookup requires a screenshot + VLM call (3-7 seconds, ~50% accuracy). Meanwhile, macOS provides a free, instant, 100%-accurate UI element tree via the Accessibility API (`AXUIElement`). We're ignoring it.

### 3.2 Design

Create a new module `src/automation_agent/perception/accessibility.py` that wraps the macOS Accessibility API.

#### New File: `perception/accessibility.py`

```python
"""macOS Accessibility API bridge for UI element discovery."""

import subprocess
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ApplicationServices import (
    AXUIElementCreateSystemWide,
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXUIElementCopyAttributeNames,
    AXUIElementPerformAction,
)
from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly, kCGNullWindowID
import AppKit


@dataclass
class AXElement:
    """Represents a macOS accessibility UI element."""
    role: str                          # "AXButton", "AXTextField", "AXStaticText", etc.
    title: Optional[str] = None        # Button label, window title
    value: Optional[str] = None        # Text field content, checkbox state
    description: Optional[str] = None  # Accessibility description
    position: Optional[Tuple[int, int]] = None   # (x, y) in screen coords
    size: Optional[Tuple[int, int]] = None       # (width, height)
    enabled: bool = True
    focused: bool = False
    children: List["AXElement"] = field(default_factory=list)
    identifier: Optional[str] = None   # AXIdentifier if available
    raw_ref: Any = None                # Raw AXUIElementRef (for performing actions)

    @property
    def center(self) -> Optional[Tuple[int, int]]:
        """Get center coordinates of element."""
        if self.position and self.size:
            return (
                self.position[0] + self.size[0] // 2,
                self.position[1] + self.size[1] // 2,
            )
        return None

    @property
    def bounds(self) -> Optional[Tuple[int, int, int, int]]:
        """Get (x1, y1, x2, y2) bounding box."""
        if self.position and self.size:
            return (
                self.position[0],
                self.position[1],
                self.position[0] + self.size[0],
                self.position[1] + self.size[1],
            )
        return None


class AccessibilityBridge:
    """
    Bridge to macOS Accessibility API.

    Provides:
    - UI element tree traversal
    - Element search by role/title/description
    - Element position lookup (pixel-accurate)
    - Frontmost app/window state

    Requires: Accessibility permission in System Settings.
    """

    def __init__(self, max_depth: int = 8):
        self.max_depth = max_depth
        self._system_wide = AXUIElementCreateSystemWide()

    def get_frontmost_app(self) -> Optional[Dict[str, Any]]:
        """Get info about the frontmost application."""
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        active_app = workspace.activeApplication()
        if not active_app:
            return None
        return {
            "name": active_app.get("NSApplicationName", ""),
            "pid": active_app.get("NSApplicationProcessIdentifier", 0),
            "bundle_id": active_app.get("NSApplicationBundleIdentifier", ""),
        }

    def get_focused_window(self) -> Optional[AXElement]:
        """Get the focused window's accessibility tree."""
        app_info = self.get_frontmost_app()
        if not app_info:
            return None

        pid = app_info["pid"]
        app_ref = AXUIElementCreateApplication(pid)

        # Get focused window
        err, window_ref = AXUIElementCopyAttributeValue(
            app_ref, "AXFocusedWindow", None
        )
        if err != 0 or window_ref is None:
            return None

        return self._parse_element(window_ref, depth=0)

    def get_element_tree(self, max_depth: Optional[int] = None) -> Optional[AXElement]:
        """
        Get the full accessibility tree of the focused window.

        Args:
            max_depth: Maximum tree depth (default: self.max_depth)

        Returns:
            Root AXElement with children populated
        """
        depth = max_depth or self.max_depth
        window = self.get_focused_window()
        if window:
            self._populate_children(window, current_depth=0, max_depth=depth)
        return window

    def find_elements(
        self,
        role: Optional[str] = None,
        title: Optional[str] = None,
        title_contains: Optional[str] = None,
        value_contains: Optional[str] = None,
        enabled_only: bool = True,
    ) -> List[AXElement]:
        """
        Find elements matching criteria in the focused window's tree.

        Args:
            role: AX role to match (e.g., "AXButton", "AXTextField")
            title: Exact title match
            title_contains: Substring match on title
            value_contains: Substring match on value
            enabled_only: Only return enabled elements

        Returns:
            List of matching AXElements with positions
        """
        tree = self.get_element_tree()
        if not tree:
            return []

        results = []
        self._search_tree(
            tree, results, role, title, title_contains,
            value_contains, enabled_only
        )
        return results

    def find_element_by_description(self, description: str) -> Optional[AXElement]:
        """
        Find element matching a natural language description.

        Uses heuristics to map description to AX attributes:
        - "search button" → role=AXButton, title_contains="search"
        - "the text field" → role=AXTextField
        - "Submit" → title_contains="Submit"

        Args:
            description: Natural language description of element

        Returns:
            Best matching AXElement or None
        """
        desc_lower = description.lower()

        # Map description keywords to AX roles
        role_map = {
            "button": "AXButton",
            "text field": "AXTextField",
            "input": "AXTextField",
            "search bar": "AXTextField",
            "checkbox": "AXCheckBox",
            "radio": "AXRadioButton",
            "link": "AXLink",
            "menu": "AXMenuItem",
            "tab": "AXTab",
            "slider": "AXSlider",
            "dropdown": "AXPopUpButton",
            "select": "AXPopUpButton",
            "image": "AXImage",
        }

        # Determine target role
        target_role = None
        for keyword, ax_role in role_map.items():
            if keyword in desc_lower:
                target_role = ax_role
                break

        # Extract likely text content
        # Remove role words to get the content description
        content_words = desc_lower
        for keyword in role_map:
            content_words = content_words.replace(keyword, "").strip()
        content_words = content_words.strip(" the a an ")

        # Search for matching elements
        candidates = self.find_elements(
            role=target_role,
            title_contains=content_words if content_words else None,
        )

        if not candidates:
            # Fallback: broader search without role filter
            candidates = self.find_elements(
                title_contains=content_words if content_words else None,
            )

        # Return best match (first enabled, visible element)
        for elem in candidates:
            if elem.position and elem.enabled:
                return elem

        return candidates[0] if candidates else None

    def get_interactive_elements(self) -> List[AXElement]:
        """
        Get all interactive elements (buttons, fields, links, etc.) in focused window.

        Returns:
            List of clickable/typeable elements with positions
        """
        interactive_roles = {
            "AXButton", "AXTextField", "AXTextArea", "AXCheckBox",
            "AXRadioButton", "AXLink", "AXPopUpButton", "AXComboBox",
            "AXSlider", "AXMenuItem", "AXTab", "AXIncrementor",
        }

        tree = self.get_element_tree()
        if not tree:
            return []

        results = []
        self._collect_by_roles(tree, interactive_roles, results)
        return results

    def serialize_tree(self, element: Optional[AXElement] = None) -> str:
        """
        Serialize element tree to a compact text representation.

        Used to provide structural context to LLMs alongside screenshots.

        Format:
            [Button "Submit" @ (450, 320) 120x40]
            [TextField "Search" value="query" @ (100, 50) 300x30]
            [StaticText "Welcome to..." @ (200, 100)]

        Returns:
            Multi-line string representation of the tree
        """
        if element is None:
            element = self.get_element_tree()
        if not element:
            return "(No accessibility tree available)"

        lines = []
        self._serialize_element(element, lines, indent=0)
        return "\n".join(lines)

    # --- Private methods ---

    def _parse_element(self, ax_ref: Any, depth: int) -> AXElement:
        """Parse a raw AXUIElementRef into an AXElement."""
        role = self._get_attr(ax_ref, "AXRole") or "Unknown"
        title = self._get_attr(ax_ref, "AXTitle")
        value = self._get_attr(ax_ref, "AXValue")
        description = self._get_attr(ax_ref, "AXDescription")
        identifier = self._get_attr(ax_ref, "AXIdentifier")
        enabled = self._get_attr(ax_ref, "AXEnabled")
        focused = self._get_attr(ax_ref, "AXFocused")

        # Get position and size
        position = None
        size = None
        pos_val = self._get_attr(ax_ref, "AXPosition")
        size_val = self._get_attr(ax_ref, "AXSize")

        if pos_val:
            position = (int(pos_val.x), int(pos_val.y))
        if size_val:
            size = (int(size_val.width), int(size_val.height))

        return AXElement(
            role=role,
            title=title,
            value=str(value) if value is not None else None,
            description=description,
            position=position,
            size=size,
            enabled=bool(enabled) if enabled is not None else True,
            focused=bool(focused) if focused is not None else False,
            identifier=identifier,
            raw_ref=ax_ref,
        )

    def _populate_children(self, element: AXElement, current_depth: int, max_depth: int):
        """Recursively populate children of an element."""
        if current_depth >= max_depth or element.raw_ref is None:
            return

        children_refs = self._get_attr(element.raw_ref, "AXChildren")
        if not children_refs:
            return

        for child_ref in children_refs:
            child = self._parse_element(child_ref, current_depth + 1)
            element.children.append(child)
            self._populate_children(child, current_depth + 1, max_depth)

    def _search_tree(self, element, results, role, title, title_contains,
                     value_contains, enabled_only):
        """Recursively search tree for matching elements."""
        match = True
        if role and element.role != role:
            match = False
        if title and element.title != title:
            match = False
        if title_contains and (not element.title or title_contains.lower() not in element.title.lower()):
            match = False
        if value_contains and (not element.value or value_contains.lower() not in element.value.lower()):
            match = False
        if enabled_only and not element.enabled:
            match = False

        if match and (role or title or title_contains or value_contains):
            results.append(element)

        for child in element.children:
            self._search_tree(child, results, role, title, title_contains,
                            value_contains, enabled_only)

    def _collect_by_roles(self, element, roles, results):
        """Collect elements matching any of the given roles."""
        if element.role in roles and element.position:
            results.append(element)
        for child in element.children:
            self._collect_by_roles(child, roles, results)

    def _serialize_element(self, element, lines, indent):
        """Serialize single element to text."""
        prefix = "  " * indent
        parts = [f"{element.role.replace('AX', '')}"]
        if element.title:
            parts.append(f'"{element.title}"')
        if element.value and len(str(element.value)) < 50:
            parts.append(f'value="{element.value}"')
        if element.position:
            parts.append(f"@ ({element.position[0]},{element.position[1]})")
        if element.size:
            parts.append(f"{element.size[0]}x{element.size[1]}")
        if not element.enabled:
            parts.append("[disabled]")

        lines.append(f"{prefix}[{' '.join(parts)}]")

        for child in element.children:
            self._serialize_element(child, lines, indent + 1)

    def _get_attr(self, ax_ref, attr_name):
        """Safely get an accessibility attribute."""
        try:
            err, value = AXUIElementCopyAttributeValue(ax_ref, attr_name, None)
            return value if err == 0 else None
        except Exception:
            return None
```

#### Integration: Modify `ScreenObserver`

The observer gets a new `accessibility` parameter and uses it as the **fast path** before falling back to vision:

```python
# In observer.py — modified find_element method

async def find_element(self, description: str) -> Optional[Coordinates]:
    """
    Find a UI element by description.

    Strategy (Mixture-of-Grounding lite):
    1. Try accessibility API first (instant, accurate)
    2. Fall back to vision model (slower, less accurate)
    """
    # FAST PATH: Accessibility API
    if self.accessibility:
        ax_element = self.accessibility.find_element_by_description(description)
        if ax_element and ax_element.center:
            cx, cy = ax_element.center
            w = ax_element.size[0] if ax_element.size else 0
            h = ax_element.size[1] if ax_element.size else 0
            return Coordinates(x=cx, y=cy, width=w, height=h)

    # SLOW PATH: Vision model
    screenshot_b64 = self.capturer.capture_screen_b64()
    prompt = f"Find this element: {description}\nReturn its bounding box."
    response = await self.vision.generate_vision(
        model=self.model,
        prompt=prompt,
        image_b64=screenshot_b64,
        system=ELEMENT_FINDER_SYSTEM_PROMPT,
    )
    return self._parse_coordinates(response)
```

#### Config Changes

```python
# In config.py — add:
use_accessibility: bool = Field(
    default=True,
    description="Use macOS Accessibility API for element discovery (requires permission)",
)
```

### 3.3 Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/automation_agent/perception/accessibility.py` | **CREATE** | New AccessibilityBridge class |
| `src/automation_agent/orchestrator/observer.py` | **MODIFY** | Add accessibility fast-path to `find_element` |
| `src/automation_agent/config.py` | **MODIFY** | Add `use_accessibility` setting |
| `src/automation_agent/__main__.py` | **MODIFY** | Initialize AccessibilityBridge, pass to observer |
| `tests/perception/test_accessibility.py` | **CREATE** | Unit tests for AccessibilityBridge |

### 3.4 Coordinate Space Consideration

**Important:** Accessibility API returns coordinates in **screen logical space** (same as PyAutoGUI). This means when we get coordinates from the accessibility bridge, we should **NOT** pass them through ClickAction's Retina scaling — they're already in the right space.

Add a `coordinate_space` field to `Coordinates`:

```python
@dataclass
class Coordinates:
    x: int
    y: int
    width: Optional[int] = None
    height: Optional[int] = None
    space: str = "screenshot"  # "screenshot" or "logical"
```

Then in `_execute_action` in `agent.py`:

```python
if action_type == "click_element":
    coords = await self.observer.find_element(description)
    if coords is None:
        return ActionResult(success=False, ...)

    if coords.space == "logical":
        # Accessibility coords — already in logical space, click directly
        pyautogui.click(coords.center_x, coords.center_y)
    else:
        # Vision coords — in screenshot space, needs Retina scaling
        click_action = ClickAction(x=coords.center_x, y=coords.center_y)
        await click_action.execute()
```

---

## 4. Phase 2: Grounding Model Upgrade (Week 1-2)

### 4.1 Problem

We use vanilla Qwen2-VL (or Qwen3-VL) prompted to return `<box>(x1,y1,x2,y2)</box>` coordinates. This general VLM was not trained for UI grounding. The heuristic `_infer_coordinate_space()` method to guess whether coordinates are normalized or pixel-space is fragile.

### 4.2 Design

Replace the vision grounding backend with **UGround-7B** (same Qwen2-VL backbone, fine-tuned on 10M UI elements). UGround returns coordinates in a consistent format, eliminating the coordinate space inference problem.

#### Option A: UGround via Ollama (Preferred)

Create an Ollama modelfile for UGround:

```
# uground.Modelfile
FROM uground-7b-q4_K_M.gguf
PARAMETER temperature 0.1
PARAMETER num_predict 256
TEMPLATE """{{ .System }}
{{ .Prompt }}"""
SYSTEM "You are a GUI element grounding model. Given a screenshot and an instruction, return the bounding box of the target element in (x1, y1, x2, y2) format with pixel coordinates."
```

```bash
ollama create uground -f uground.Modelfile
```

Then in config:
```python
grounding_model: str = Field(
    default="uground-7b",
    description="Specialized grounding model for UI element location",
)
```

#### Option B: UGround via HuggingFace (Fallback)

New client in `llm/uground_client.py`:

```python
"""UGround vision grounding client."""

from transformers import AutoModelForCausalLM, AutoProcessor
import torch
from typing import Optional, Tuple


class UGroundClient:
    """
    Client for UGround-V1 UI grounding model.

    UGround is fine-tuned on 10M UI elements from 1.3M screenshots.
    Returns pixel coordinates directly (no normalization ambiguity).
    """

    def __init__(self, model_name: str = "osu-nlp-group/UGround-V1-7B"):
        self.model_name = model_name
        self._model = None
        self._processor = None

    def _load_model(self):
        if self._model is None:
            self._processor = AutoProcessor.from_pretrained(self.model_name)
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_name,
                torch_dtype=torch.float16,
                device_map="auto",
            )

    async def ground_element(
        self,
        image_b64: str,
        instruction: str,
    ) -> Optional[Tuple[int, int, int, int]]:
        """
        Find element in screenshot matching instruction.

        Args:
            image_b64: Base64-encoded screenshot
            instruction: Natural language description of target element

        Returns:
            (x1, y1, x2, y2) pixel coordinates or None
        """
        self._load_model()
        # ... model inference, return pixel coordinates
```

#### Observer Integration

Modify `ScreenObserver` to use the grounding model separately from the general vision model:

```python
class ScreenObserver:
    def __init__(
        self,
        vision_client: Any,
        capturer: ScreenCapturer,
        model: str = "qwen3-vl",
        grounding_client: Optional[Any] = None,  # NEW
        grounding_model: Optional[str] = None,    # NEW
        accessibility: Optional[AccessibilityBridge] = None,  # From Phase 1
    ):
        self.vision = vision_client
        self.capturer = capturer
        self.model = model
        self.grounding = grounding_client or vision_client  # Fallback to vision
        self.grounding_model = grounding_model or model
        self.accessibility = accessibility
```

Now `find_element` uses three strategies:

```python
async def find_element(self, description: str) -> Optional[Coordinates]:
    """
    Multi-strategy element finding (Mixture-of-Grounding lite):

    1. Accessibility API (instant, ~95% accurate for standard widgets)
    2. UGround grounding model (~85% accurate for anything visual)
    3. General VLM fallback (~50% accurate)
    """
    # Strategy 1: Accessibility
    if self.accessibility:
        ax_elem = self.accessibility.find_element_by_description(description)
        if ax_elem and ax_elem.center:
            return Coordinates(*ax_elem.center, *ax_elem.size, space="logical")

    # Strategy 2: Specialized grounding model
    if self.grounding != self.vision or self.grounding_model != self.model:
        screenshot_b64 = self.capturer.capture_screen_b64()
        response = await self.grounding.generate_vision(
            model=self.grounding_model,
            prompt=f"Find and click on: {description}",
            image_b64=screenshot_b64,
        )
        coords = self._parse_grounding_response(response)
        if coords:
            return coords

    # Strategy 3: General VLM fallback
    screenshot_b64 = self.capturer.capture_screen_b64()
    response = await self.vision.generate_vision(
        model=self.model,
        prompt=f"Find this element: {description}\nReturn its bounding box.",
        image_b64=screenshot_b64,
        system=ELEMENT_FINDER_SYSTEM_PROMPT,
    )
    return self._parse_coordinates(response)
```

### 4.3 Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/automation_agent/llm/uground_client.py` | **CREATE** | UGround model client |
| `src/automation_agent/orchestrator/observer.py` | **MODIFY** | Add grounding_client, 3-strategy find_element |
| `src/automation_agent/config.py` | **MODIFY** | Add `grounding_model` setting |
| `src/automation_agent/__main__.py` | **MODIFY** | Initialize grounding client |
| `tests/llm/test_uground_client.py` | **CREATE** | UGround client tests |

---

## 5. Phase 3: Action Verification & Retry (Week 2)

### 5.1 Problem

Currently, after executing an action (click, type, etc.), we have no idea if it worked. We just wait `action_delay` seconds and move to the next observation cycle. If a click missed, we don't know until the next full observe-think-act cycle (3-7 seconds later), and even then the LLM might not notice.

VyUI has real-time error detection. Agent S2 has hierarchical replanning. We have nothing.

### 5.2 Design

Add an `ActionVerifier` that checks whether an action had the expected effect.

#### New File: `orchestrator/verifier.py`

```python
"""Action verification via screenshot diffing and condition checking."""

import numpy as np
from typing import Optional, Tuple
from PIL import Image
import io
import base64

from ..perception.capture import ScreenCapturer


class ActionVerifier:
    """
    Verifies that actions had their expected effect.

    Strategies:
    1. Screenshot diff — did the screen change at all?
    2. Region diff — did the area around the click target change?
    3. Condition check — is an expected state now visible?
    """

    def __init__(self, capturer: ScreenCapturer, change_threshold: float = 0.01):
        """
        Args:
            capturer: Screen capturer for taking verification screenshots
            change_threshold: Minimum pixel change ratio to consider "changed" (0.0-1.0)
        """
        self.capturer = capturer
        self.change_threshold = change_threshold
        self._pre_action_screenshot: Optional[np.ndarray] = None

    def capture_before(self) -> None:
        """Capture screenshot before action for later comparison."""
        img = self.capturer.capture_screen()
        self._pre_action_screenshot = np.array(img)

    def verify_screen_changed(self) -> bool:
        """
        Check if the screen changed since capture_before().

        Returns:
            True if screen changed meaningfully, False if identical
        """
        if self._pre_action_screenshot is None:
            return True  # No baseline, assume changed

        post_img = np.array(self.capturer.capture_screen())

        if self._pre_action_screenshot.shape != post_img.shape:
            return True  # Resolution changed, definitely different

        # Calculate pixel-wise difference
        diff = np.abs(
            self._pre_action_screenshot.astype(float) - post_img.astype(float)
        )
        change_ratio = np.mean(diff > 10) # Pixels that changed by >10 (out of 255)

        return change_ratio > self.change_threshold

    def verify_region_changed(
        self, x: int, y: int, radius: int = 100
    ) -> bool:
        """
        Check if the region around (x, y) changed.

        More targeted than full-screen diff — catches cases where a
        button changed state but the rest of the screen didn't.

        Args:
            x, y: Center of region to check (in screenshot space)
            radius: Radius of region to check

        Returns:
            True if region changed
        """
        if self._pre_action_screenshot is None:
            return True

        post_img = np.array(self.capturer.capture_screen())

        if self._pre_action_screenshot.shape != post_img.shape:
            return True

        h, w = self._pre_action_screenshot.shape[:2]
        x1 = max(0, x - radius)
        y1 = max(0, y - radius)
        x2 = min(w, x + radius)
        y2 = min(h, y + radius)

        pre_region = self._pre_action_screenshot[y1:y2, x1:x2]
        post_region = post_img[y1:y2, x1:x2]

        diff = np.abs(pre_region.astype(float) - post_region.astype(float))
        change_ratio = np.mean(diff > 10)

        return change_ratio > self.change_threshold

    def get_change_summary(self) -> dict:
        """Get detailed change information for logging."""
        if self._pre_action_screenshot is None:
            return {"has_baseline": False}

        post_img = np.array(self.capturer.capture_screen())

        if self._pre_action_screenshot.shape != post_img.shape:
            return {"has_baseline": True, "resolution_changed": True}

        diff = np.abs(
            self._pre_action_screenshot.astype(float) - post_img.astype(float)
        )

        return {
            "has_baseline": True,
            "overall_change_ratio": float(np.mean(diff > 10)),
            "max_pixel_change": int(np.max(diff)),
            "mean_pixel_change": float(np.mean(diff)),
        }
```

#### Integrate into Agent Loop

Modify `_execute_action` in `agent.py`:

```python
async def _execute_action(self, next_action: NextAction) -> ActionResult:
    """Execute action with verification and retry."""

    max_retries = 3

    for attempt in range(max_retries):
        # Capture before-screenshot
        if self.verifier:
            self.verifier.capture_before()

        # Execute the action (existing logic)
        result = await self._do_execute_action(next_action)

        if not result.success:
            # Action itself failed (e.g., element not found)
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5)
                continue
            return result

        # Verify the action had an effect
        if self.verifier and next_action.action in ("click", "click_element"):
            await asyncio.sleep(0.3)  # Brief wait for UI to update

            # Check if screen changed around click target
            click_x = result.params.get("x", 0)
            click_y = result.params.get("y", 0)

            if not self.verifier.verify_region_changed(click_x, click_y):
                if attempt < max_retries - 1:
                    # Screen didn't change — click probably missed
                    result.success = False
                    result.error = f"Click at ({click_x},{click_y}) had no visible effect (attempt {attempt+1})"
                    await asyncio.sleep(0.5)
                    continue

        return result

    return result  # Return last attempt's result
```

### 5.3 Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/automation_agent/orchestrator/verifier.py` | **CREATE** | ActionVerifier with screenshot diffing |
| `src/automation_agent/orchestrator/agent.py` | **MODIFY** | Add verification + retry loop around action execution |
| `tests/orchestrator/test_verifier.py` | **CREATE** | Verification tests |

---

## 6. Phase 4: Context Monitor (Week 2-3)

### 6.1 Problem

Every iteration of our agentic loop takes a fresh screenshot and asks the VLM to describe the entire screen from scratch. This is:
- **Slow** (3-7s per observation)
- **Wasteful** (re-describing unchanged content)
- **Context-losing** (no memory of what was where 2 steps ago)

VyUI's Context Monitor continuously tracks window states, selected elements, and screen regions.

### 6.2 Design

Add a `ContextMonitor` that maintains a running model of the desktop state, updated cheaply via accessibility APIs, with full vision refreshes only when needed.

#### New File: `orchestrator/context.py`

```python
"""Persistent context monitor for desktop state tracking."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..perception.accessibility import AccessibilityBridge, AXElement


@dataclass
class WindowState:
    """Snapshot of a window's state."""
    app_name: str
    window_title: str
    bounds: Optional[tuple] = None  # (x, y, w, h)
    is_focused: bool = False
    url: Optional[str] = None  # For browser windows
    interactive_elements: List[AXElement] = field(default_factory=list)
    last_updated: datetime = field(default_factory=datetime.now)


@dataclass
class DesktopContext:
    """Full snapshot of the desktop state."""
    frontmost_app: str = ""
    frontmost_window: Optional[WindowState] = None
    recently_clicked: Optional[Dict[str, Any]] = None  # Last click target + coords
    recently_typed: Optional[str] = None  # Last typed text
    form_fields_filled: Dict[str, str] = field(default_factory=dict)  # field_name → value
    navigation_history: List[str] = field(default_factory=list)  # URLs visited
    last_vision_description: str = ""  # Last full VLM description
    iteration_count: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


class ContextMonitor:
    """
    Maintains persistent desktop context across agent iterations.

    Design principles:
    - Cheap updates via Accessibility API (no VLM call needed)
    - Full vision refresh only when context is stale or insufficient
    - Tracks what's changed between iterations
    - Provides rich context to the planning LLM
    """

    def __init__(self, accessibility: Optional[AccessibilityBridge] = None):
        self.accessibility = accessibility
        self.context = DesktopContext()
        self._previous_context: Optional[DesktopContext] = None

    def update_cheap(self) -> DesktopContext:
        """
        Update context cheaply (no VLM call).
        Uses Accessibility API for window state + element inventory.

        ~50ms vs 3-7s for a full vision observation.
        """
        self._previous_context = self.context

        if self.accessibility:
            app_info = self.accessibility.get_frontmost_app()
            if app_info:
                self.context.frontmost_app = app_info["name"]

            window = self.accessibility.get_focused_window()
            if window:
                interactive = self.accessibility.get_interactive_elements()
                self.context.frontmost_window = WindowState(
                    app_name=self.context.frontmost_app,
                    window_title=window.title or "",
                    bounds=window.bounds,
                    is_focused=True,
                    interactive_elements=interactive,
                )

        self.context.iteration_count += 1
        self.context.timestamp = datetime.now()
        return self.context

    def update_with_vision(self, vision_description: str) -> None:
        """Update context with a new VLM observation."""
        self.context.last_vision_description = vision_description

    def record_click(self, target: str, x: int, y: int) -> None:
        """Record a click action for context."""
        self.context.recently_clicked = {
            "target": target, "x": x, "y": y,
            "timestamp": datetime.now().isoformat(),
        }

    def record_type(self, text: str, field_name: Optional[str] = None) -> None:
        """Record a type action for context."""
        self.context.recently_typed = text
        if field_name:
            self.context.form_fields_filled[field_name] = text

    def record_navigation(self, url: str) -> None:
        """Record URL navigation."""
        self.context.navigation_history.append(url)

    def needs_full_vision(self) -> bool:
        """
        Determine if a full VLM observation is needed.

        Returns True when:
        - No previous vision description exists
        - App/window changed since last observation
        - Last action was navigation (page likely changed)
        - Too many iterations since last vision refresh
        """
        if not self.context.last_vision_description:
            return True

        if self._previous_context:
            if self.context.frontmost_app != self._previous_context.frontmost_app:
                return True

        if self.context.recently_clicked and "url" in str(self.context.recently_clicked.get("target", "")).lower():
            return True

        # Refresh every 3 iterations at minimum
        if self.context.iteration_count % 3 == 0:
            return True

        return False

    def format_for_planner(self) -> str:
        """
        Format current context as text for the planning LLM prompt.

        This replaces the raw history text, providing structured context.
        """
        lines = []
        lines.append(f"## Current Desktop State")
        lines.append(f"App: {self.context.frontmost_app}")

        if self.context.frontmost_window:
            w = self.context.frontmost_window
            lines.append(f"Window: {w.window_title}")
            if w.interactive_elements:
                lines.append(f"Interactive elements ({len(w.interactive_elements)}):")
                for elem in w.interactive_elements[:20]:  # Cap at 20
                    label = elem.title or elem.description or elem.role
                    lines.append(f"  - [{elem.role.replace('AX','')}] {label}")

        if self.context.last_vision_description:
            lines.append(f"\n## Screen Description")
            lines.append(self.context.last_vision_description[:500])

        if self.context.recently_clicked:
            lines.append(f"\n## Last Action")
            lines.append(f"Clicked: {self.context.recently_clicked['target']}")

        if self.context.recently_typed:
            lines.append(f"Typed: {self.context.recently_typed}")

        if self.context.form_fields_filled:
            lines.append(f"\n## Form Progress")
            for field, value in self.context.form_fields_filled.items():
                lines.append(f"  {field}: {value}")

        if self.context.navigation_history:
            lines.append(f"\n## Navigation History")
            for url in self.context.navigation_history[-5:]:
                lines.append(f"  → {url}")

        return "\n".join(lines)
```

#### Integrate into Agent Loop

The agentic loop changes from "always observe with VLM" to "cheap update + selective VLM":

```python
async def _execute_agentic(self, goal: str, intent: Intent) -> ExecutionResult:
    # ... (initial steps unchanged) ...

    for iteration in range(self.max_iterations):
        # CHEAP UPDATE: Accessibility-based context refresh (~50ms)
        if self.context_monitor:
            self.context_monitor.update_cheap()

        # SELECTIVE VISION: Only call VLM when needed (~3-7s)
        if not self.context_monitor or self.context_monitor.needs_full_vision():
            observation = await self.observer.observe(
                "Describe the current screen state."
            )
            if self.context_monitor:
                self.context_monitor.update_with_vision(observation.description)
            history.append(HistoryEntry.from_observation(observation))

        # THINK: Plan next action with rich context
        if self.context_monitor:
            context_text = self.context_monitor.format_for_planner()
            next_action = await self._plan_next_action_with_context(goal, context_text, history)
        else:
            next_action = await self._plan_next_action(goal, history)

        # CHECK & ACT (unchanged)
        ...

        # RECORD: Update context with action taken
        if self.context_monitor and result.success:
            if next_action.action == "click_element":
                self.context_monitor.record_click(
                    next_action.params.get("description", ""),
                    result.params.get("x", 0),
                    result.params.get("y", 0),
                )
            elif next_action.action == "type_text":
                self.context_monitor.record_type(next_action.params.get("text", ""))
            elif next_action.action == "open_url":
                self.context_monitor.record_navigation(next_action.params.get("url", ""))
```

### 6.3 Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/automation_agent/orchestrator/context.py` | **CREATE** | ContextMonitor with cheap updates |
| `src/automation_agent/orchestrator/agent.py` | **MODIFY** | Integrate context monitor into agentic loop |
| `tests/orchestrator/test_context.py` | **CREATE** | Context monitor tests |

---

## 7. Phase 5: Mixture-of-Grounding Router (Week 3-4)

### 7.1 Problem

Even with accessibility + UGround, no single grounding strategy works for all elements:
- Accessibility works great for standard widgets, fails for web canvas content
- Vision grounding works for visual elements, fails for identical-looking buttons
- OCR works for finding text, fails for icons

Agent S2 proved that **routing to the right expert** is the key insight.

### 7.2 Design

Add a `GroundingRouter` that picks the best strategy per element.

#### New File: `orchestrator/grounding.py`

```python
"""Mixture-of-Grounding: route element finding to the best expert."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from ..perception.accessibility import AccessibilityBridge
from ..perception.capture import ScreenCapturer
from .models import Coordinates


class GroundingStrategy(Enum):
    ACCESSIBILITY = "accessibility"   # AXUIElement tree (buttons, fields, etc.)
    VISION = "vision"                 # Screenshot + grounding model (any visual element)
    OCR = "ocr"                       # Text recognition (text-based elements)
    HYBRID = "hybrid"                 # Accessibility + vision confirmation


@dataclass
class GroundingResult:
    """Result from a grounding expert."""
    coordinates: Optional[Coordinates]
    strategy_used: GroundingStrategy
    confidence: float  # 0.0-1.0
    element_info: Optional[dict] = None  # Extra metadata from the expert


class GroundingRouter:
    """
    Routes element-finding requests to the best grounding expert.

    Inspired by Agent S2's Mixture-of-Grounding (MoG).

    Decision logic:
    1. Parse the element description to determine type
    2. Route to the most appropriate expert
    3. If first expert fails, try fallback experts
    """

    def __init__(
        self,
        accessibility: Optional[AccessibilityBridge] = None,
        vision_client: Any = None,
        vision_model: str = "uground-7b",
        grounding_client: Any = None,
        grounding_model: str = "uground-7b",
        capturer: Optional[ScreenCapturer] = None,
    ):
        self.accessibility = accessibility
        self.vision = vision_client
        self.vision_model = vision_model
        self.grounding = grounding_client
        self.grounding_model = grounding_model
        self.capturer = capturer

    def classify_element(self, description: str) -> list[GroundingStrategy]:
        """
        Classify element description and return ordered list of strategies to try.

        Args:
            description: Natural language description like "the Submit button"

        Returns:
            Ordered list of strategies, best first
        """
        desc_lower = description.lower()

        # Standard widgets → Accessibility first
        widget_keywords = [
            "button", "text field", "input", "checkbox", "radio",
            "dropdown", "select", "menu", "tab", "slider", "link",
            "search bar", "close button", "submit",
        ]
        if any(kw in desc_lower for kw in widget_keywords):
            return [
                GroundingStrategy.ACCESSIBILITY,
                GroundingStrategy.VISION,
                GroundingStrategy.OCR,
            ]

        # Text-specific → OCR first
        text_keywords = [
            "text that says", "label", "heading", "title",
            "paragraph", "price", "number",
        ]
        if any(kw in desc_lower for kw in text_keywords):
            return [
                GroundingStrategy.OCR,
                GroundingStrategy.VISION,
                GroundingStrategy.ACCESSIBILITY,
            ]

        # Visual elements → Vision first
        visual_keywords = [
            "image", "icon", "thumbnail", "video", "logo",
            "avatar", "chart", "graph", "map",
        ]
        if any(kw in desc_lower for kw in visual_keywords):
            return [
                GroundingStrategy.VISION,
                GroundingStrategy.ACCESSIBILITY,
            ]

        # Position-based → Vision (accessibility has positions but less spatial reasoning)
        position_keywords = [
            "first", "second", "third", "top", "bottom",
            "left", "right", "middle", "closest", "nearest",
            "most popular", "highest", "lowest",
        ]
        if any(kw in desc_lower for kw in position_keywords):
            return [
                GroundingStrategy.VISION,
                GroundingStrategy.ACCESSIBILITY,
            ]

        # Default: try accessibility then vision
        return [
            GroundingStrategy.ACCESSIBILITY,
            GroundingStrategy.VISION,
            GroundingStrategy.OCR,
        ]

    async def find_element(self, description: str) -> Optional[GroundingResult]:
        """
        Find element using the best available strategy.

        Tries strategies in order, returns first successful result.
        """
        strategies = self.classify_element(description)

        for strategy in strategies:
            result = await self._try_strategy(strategy, description)
            if result and result.coordinates:
                return result

        return None

    async def _try_strategy(
        self, strategy: GroundingStrategy, description: str
    ) -> Optional[GroundingResult]:
        """Execute a single grounding strategy."""

        if strategy == GroundingStrategy.ACCESSIBILITY:
            return await self._ground_accessibility(description)
        elif strategy == GroundingStrategy.VISION:
            return await self._ground_vision(description)
        elif strategy == GroundingStrategy.OCR:
            return await self._ground_ocr(description)
        elif strategy == GroundingStrategy.HYBRID:
            return await self._ground_hybrid(description)

        return None

    async def _ground_accessibility(self, description: str) -> Optional[GroundingResult]:
        """Find element via Accessibility API."""
        if not self.accessibility:
            return None

        elem = self.accessibility.find_element_by_description(description)
        if not elem or not elem.center:
            return None

        cx, cy = elem.center
        w = elem.size[0] if elem.size else 0
        h = elem.size[1] if elem.size else 0

        return GroundingResult(
            coordinates=Coordinates(x=cx, y=cy, width=w, height=h, space="logical"),
            strategy_used=GroundingStrategy.ACCESSIBILITY,
            confidence=0.95,
            element_info={
                "role": elem.role,
                "title": elem.title,
                "enabled": elem.enabled,
            },
        )

    async def _ground_vision(self, description: str) -> Optional[GroundingResult]:
        """Find element via vision grounding model."""
        if not self.capturer or not self.grounding:
            return None

        screenshot_b64 = self.capturer.capture_screen_b64()

        response = await self.grounding.generate_vision(
            model=self.grounding_model,
            prompt=f"Find and locate: {description}",
            image_b64=screenshot_b64,
        )

        coords = self._parse_vision_response(response)
        if not coords:
            return None

        return GroundingResult(
            coordinates=coords,
            strategy_used=GroundingStrategy.VISION,
            confidence=0.80,
        )

    async def _ground_ocr(self, description: str) -> Optional[GroundingResult]:
        """Find element via OCR text matching."""
        if not self.capturer:
            return None

        # Use pytesseract to find text on screen
        import pytesseract
        from PIL import Image

        img = self.capturer.capture_screen()
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

        # Extract target text from description
        target_text = self._extract_target_text(description)
        if not target_text:
            return None

        # Find best matching text region
        best_match = None
        best_score = 0

        for i, text in enumerate(data["text"]):
            if not text.strip():
                continue
            score = self._text_similarity(target_text, text)
            if score > best_score and score > 0.5:
                best_score = score
                best_match = i

        if best_match is None:
            return None

        x = data["left"][best_match]
        y = data["top"][best_match]
        w = data["width"][best_match]
        h = data["height"][best_match]

        return GroundingResult(
            coordinates=Coordinates(
                x=x + w // 2, y=y + h // 2, width=w, height=h, space="screenshot"
            ),
            strategy_used=GroundingStrategy.OCR,
            confidence=best_score,
            element_info={"matched_text": data["text"][best_match]},
        )

    async def _ground_hybrid(self, description: str) -> Optional[GroundingResult]:
        """Use accessibility to narrow region, then vision to confirm."""
        ax_result = await self._ground_accessibility(description)
        if not ax_result:
            return await self._ground_vision(description)

        # TODO: Use accessibility coordinates to crop screenshot region,
        # then run vision on just that region for confirmation
        return ax_result

    def _parse_vision_response(self, response: str) -> Optional[Coordinates]:
        """Parse coordinates from vision model response."""
        import re
        match = re.search(
            r"<box>\s*\(?\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)?\s*</box>",
            response,
        )
        if not match:
            return None

        x1, y1, x2, y2 = [int(match.group(i)) for i in range(1, 5)]
        return Coordinates.from_bbox(x1, y1, x2, y2)

    def _extract_target_text(self, description: str) -> Optional[str]:
        """Extract the text content to search for from a description."""
        # Simple heuristic: text in quotes, or after "says"/"labeled"
        import re

        quote_match = re.search(r'"([^"]+)"', description)
        if quote_match:
            return quote_match.group(1)

        says_match = re.search(r'(?:says|labeled|titled|named)\s+"?([^"]+)"?', description.lower())
        if says_match:
            return says_match.group(1)

        return None

    def _text_similarity(self, a: str, b: str) -> float:
        """Simple text similarity score (0.0-1.0)."""
        a_lower, b_lower = a.lower().strip(), b.lower().strip()
        if a_lower == b_lower:
            return 1.0
        if a_lower in b_lower or b_lower in a_lower:
            return 0.8
        # Word overlap
        a_words = set(a_lower.split())
        b_words = set(b_lower.split())
        if not a_words or not b_words:
            return 0.0
        overlap = len(a_words & b_words)
        return overlap / max(len(a_words), len(b_words))
```

### 7.3 Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/automation_agent/orchestrator/grounding.py` | **CREATE** | GroundingRouter with multi-expert routing |
| `src/automation_agent/orchestrator/agent.py` | **MODIFY** | Use GroundingRouter instead of direct observer.find_element |
| `tests/orchestrator/test_grounding.py` | **CREATE** | Grounding router tests |

---

## 8. Phase 6: Enhanced Planning with Structured Context (Week 3-4)

### 8.1 Problem

Our planning prompt sends raw history text (last 10 entries) to the LLM. It doesn't include the accessibility tree structure, element inventory, or form progress. The LLM has to re-derive context from natural language descriptions.

### 8.2 Design

Modify `_plan_next_action` to include structured context from the ContextMonitor and accessibility tree:

```python
async def _plan_next_action_with_context(
    self, goal: str, context_text: str, history: List[HistoryEntry]
) -> NextAction:
    """
    Plan next action with rich structured context.

    The planning prompt includes:
    1. Goal
    2. Structured desktop context (app, window, elements, form progress)
    3. Recent history (last 5 entries)
    4. Available actions
    """
    history_text = self._format_history(history[-5:])  # Shorter — context fills the gap

    prompt = f"""Goal: {goal}

{context_text}

## Recent History
{history_text}

Based on the current state and goal, what is the next action?"""

    response = await self.llm.generate(
        model=self.text_model,
        prompt=prompt,
        system=AGENT_PLANNER_SYSTEM_PROMPT,
        format="json",
    )

    return self._parse_next_action(response)
```

The key improvement: the planner now sees **structured element inventory** from accessibility, not just VLM's prose description. For example:

**Before (VLM description only):**
```
"I see a web page with several buttons and a search bar at the top..."
```

**After (structured context):**
```
## Current Desktop State
App: Safari
Window: OpenTable - Book restaurants

Interactive elements (12):
  - [Button] "Find a Table"
  - [TextField] "Restaurant or Cuisine" value="sushi"
  - [PopUpButton] "Party Size" value="2 people"
  - [TextField] "Date" value="Today"
  - [TextField] "Time" value="7:00 PM"
  - [Button] "Let's go"
  - [Link] "San Jose, CA"
  ...

## Form Progress
  Restaurant: sushi
  Party Size: 2
  Date: Today
  Time: 7:00 PM
```

The planner can now make much better decisions because it has the exact element names and current values.

---

## 9. Implementation Sequence & Dependencies

```
Week 1:
┌─────────────────────┐     ┌──────────────────────┐
│ Phase 1:            │     │ Phase 2:             │
│ Accessibility Bridge │ ──▶ │ Grounding Model      │
│ (perception/        │     │ Upgrade              │
│  accessibility.py)  │     │ (llm/uground_client) │
└─────────────────────┘     └──────────────────────┘
          │                           │
          ▼                           ▼
Week 2:
┌─────────────────────┐     ┌──────────────────────┐
│ Phase 3:            │     │ Phase 4:             │
│ Action Verification  │     │ Context Monitor      │
│ (orchestrator/      │     │ (orchestrator/       │
│  verifier.py)       │     │  context.py)         │
└─────────────────────┘     └──────────────────────┘
          │                           │
          └─────────┬─────────────────┘
                    ▼
Week 3-4:
┌──────────────────────────────────────┐
│ Phase 5: Mixture-of-Grounding Router │
│ (orchestrator/grounding.py)          │
│ + Phase 6: Enhanced Planning         │
└──────────────────────────────────────┘
```

### Dependencies

| Phase | Depends On | Can Start After |
|-------|-----------|-----------------|
| Phase 1 (Accessibility) | Nothing | Immediately |
| Phase 2 (Grounding Model) | Nothing | Immediately (parallel with Phase 1) |
| Phase 3 (Verification) | Nothing | Immediately (parallel) |
| Phase 4 (Context Monitor) | Phase 1 (uses AccessibilityBridge) | Phase 1 complete |
| Phase 5 (MoG Router) | Phases 1 + 2 | Phases 1 & 2 complete |
| Phase 6 (Enhanced Planning) | Phase 4 | Phase 4 complete |

**Phases 1, 2, and 3 can be developed in parallel.**

---

## 10. New File Inventory

| File | Phase | Lines (est.) | Purpose |
|------|-------|-------------|---------|
| `src/automation_agent/perception/accessibility.py` | 1 | ~300 | macOS Accessibility API bridge |
| `src/automation_agent/llm/uground_client.py` | 2 | ~150 | UGround grounding model client |
| `src/automation_agent/orchestrator/verifier.py` | 3 | ~120 | Action verification via screenshot diff |
| `src/automation_agent/orchestrator/context.py` | 4 | ~200 | Persistent context monitor |
| `src/automation_agent/orchestrator/grounding.py` | 5 | ~250 | Mixture-of-Grounding router |
| `tests/perception/test_accessibility.py` | 1 | ~100 | Accessibility bridge tests |
| `tests/llm/test_uground_client.py` | 2 | ~80 | UGround client tests |
| `tests/orchestrator/test_verifier.py` | 3 | ~80 | Verifier tests |
| `tests/orchestrator/test_context.py` | 4 | ~100 | Context monitor tests |
| `tests/orchestrator/test_grounding.py` | 5 | ~120 | Grounding router tests |
| **Total new** | | **~1,500** | |

### Modified Files

| File | Phases | Changes |
|------|--------|---------|
| `src/automation_agent/config.py` | 1,2 | Add `use_accessibility`, `grounding_model` settings |
| `src/automation_agent/orchestrator/models.py` | 1 | Add `space` field to `Coordinates` |
| `src/automation_agent/orchestrator/observer.py` | 1,2 | Add accessibility + grounding client, multi-strategy find |
| `src/automation_agent/orchestrator/agent.py` | 3,4,5,6 | Verification loop, context monitor, MoG router, enhanced planning |
| `src/automation_agent/__main__.py` | 1,2,3,4,5 | Initialize new components |

---

## 11. Migration Strategy

### Backward Compatibility

All new features are **opt-in** and **additive**:

- Accessibility bridge: only used if permission is granted and `use_accessibility=true`
- UGround: only used if model is available; falls back to existing Qwen2-VL
- Verifier: only used if initialized; agent loop works unchanged without it
- Context monitor: only used if initialized; agent loop falls back to raw history
- Grounding router: replaces `observer.find_element` calls, but router itself falls back through strategies gracefully

### Feature Flags

```bash
# Use everything (recommended)
AGENT_USE_ACCESSIBILITY=true
AGENT_GROUNDING_MODEL=uground-7b
AGENT_USE_VERIFICATION=true
AGENT_USE_CONTEXT_MONITOR=true

# Use only accessibility (fastest, no model changes)
AGENT_USE_ACCESSIBILITY=true

# Use only grounding model upgrade (best accuracy improvement)
AGENT_GROUNDING_MODEL=uground-7b

# Legacy mode (current behavior, no changes)
# (just don't set any of the above)
```

### Testing Strategy

Each phase has its own test suite. Run the full suite after each phase to ensure no regressions:

```bash
# After each phase:
pytest tests/ -v

# Integration test (requires accessibility permission + models):
pytest tests/test_integration.py -v -k "agentic"

# Benchmark grounding accuracy:
python scripts/benchmark_grounding.py --strategies accessibility,vision,ocr,hybrid
```

---

## 12. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Accessibility permission not granted by user | Medium | High (Phase 1 unusable) | Graceful fallback to vision-only; clear error message |
| UGround not available as Ollama model | Medium | Medium (Phase 2 blocked) | Support HuggingFace fallback; Qwen2-VL still works |
| Accessibility tree is empty for web content | High | Medium | Web content in browsers has limited AX data; vision handles this |
| Screenshot diff false positives (cursor blink, animations) | Medium | Low | Configurable threshold; region-based diffing reduces noise |
| Multiple grounding experts add latency | Medium | Medium | Route to ONE expert first; only try fallbacks on failure |
| Context monitor grows stale | Low | Low | Force full vision refresh every 3 iterations |
| pyobjc Accessibility API is complex/fragile | Medium | Medium | Wrap in try/except; fall back gracefully on any AX error |

---

## 13. Success Criteria

### Phase 1 Complete When:
- [ ] AccessibilityBridge can enumerate interactive elements in Safari
- [ ] AccessibilityBridge can find buttons by title
- [ ] Observer uses accessibility as fast-path before vision
- [ ] Coordinates from accessibility are used correctly (no Retina double-scaling)
- [ ] All existing tests still pass

### Phase 2 Complete When:
- [ ] UGround model loaded and running via Ollama or HuggingFace
- [ ] Observer routes grounding requests to UGround
- [ ] Coordinate parsing works without `_infer_coordinate_space` heuristics
- [ ] Grounding accuracy measurably improved on 10 test screenshots

### Phase 3 Complete When:
- [ ] ActionVerifier detects when clicks have no visible effect
- [ ] Failed clicks are retried up to 3 times
- [ ] Verification adds <500ms latency per action
- [ ] Action success rate measurably improved on test tasks

### Phase 4 Complete When:
- [ ] ContextMonitor tracks frontmost app and window
- [ ] Cheap updates take <100ms
- [ ] Full vision refreshes only happen every 3rd iteration (or on app/window change)
- [ ] Planner receives structured context instead of raw history

### Phase 5 Complete When:
- [ ] GroundingRouter correctly classifies element types
- [ ] Router tries multiple strategies in order
- [ ] "button" descriptions route to accessibility first
- [ ] "thumbnail" descriptions route to vision first
- [ ] End-to-end task completion improved on OpenTable booking test

### Phase 6 Complete When:
- [ ] Planner prompt includes element inventory from accessibility
- [ ] Planner can reference elements by exact name (not vague description)
- [ ] Form filling tasks complete with fewer iterations
- [ ] History window reduced from 10 to 5 entries (context fills the gap)
