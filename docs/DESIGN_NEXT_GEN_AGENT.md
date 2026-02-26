# Design Document: Next-Generation macOS Automation Agent

*Transforming our agent with learnings from VyUI, Agent S2, and the open-source ecosystem*

---

## 1. Objectives

Transform the current `macos-automation-agent` from a single-model, vision-only agent into a **multi-expert, accessibility-aware, context-persistent** agent that approaches the reliability levels demonstrated by VyUI (~92% grounding accuracy) and Agent S2 (#1 on OSWorld).

### Target Metrics

| Metric | Current | Target | How |
|--------|---------|--------|-----|
| UI element grounding accuracy | ~40-60% | ~85% | UGround + accessibility hybrid |
| Action success rate (per-click) | ~50-70% | ~90% | Verification + retry (already have retry_different) |
| End-to-end task completion | ~30-40% | ~70% | All improvements combined |
| Observation latency | 3-7s | 1-2s | Accessibility fast-path |
| Max iterations needed | 20 | 10 | Better grounding = fewer retries |

---

## 2. Architecture Overview

### Current Architecture (Post-Refactor)

Our agent now has a component-based architecture with Planner, SkillRegistry, VisionCoordinator, Actuator, and StepVerifier. The flow is:

```
User Goal --> SkillRegistry.match() --> Planner.plan() --> Execute Steps
                                                              |
                                                    +------------------+
                                                    | For each step:   |
                                                    | 1. Find element  |
                                                    |    (vision only) |
                                                    | 2. Execute action|
                                                    | 3. Verify result |
                                                    | 4. Retry/replan  |
                                                    |    on failure    |
                                                    +------------------+
```

### Proposed Architecture

```
User Goal --> SkillRegistry.match() --> Planner.plan() --> Execute Steps
                                                              |
                                                    +-------------------+
                                                    | For each step:    |
                                                    | 1. Context update |
                                                    |    (cheap AX poll)|
                                                    | 2. Find element   |
                                                    |    (MoG Router)   |
                                                    | 3. Execute action |
                                                    | 4. Screenshot diff|
                                                    |    verify         |
                                                    | 5. Retry/replan   |
                                                    +-------------------+
                                                              |
                                              +---------------+-----------+
                                              |               |           |
                                        +-----v-----+  +-----v---+ +----v----+
                                        |Accessibility| | Vision  | |  OCR   |
                                        |  Expert    | | Expert  | | Expert |
                                        | (AXUIElem) | |(UGround)| |(Tess.) |
                                        +------------+ +---------+ +--------+
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

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ApplicationServices import (
    AXUIElementCreateSystemWide,
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
)
import AppKit


@dataclass
class AXElement:
    """Represents a macOS accessibility UI element."""
    role: str                          # "AXButton", "AXTextField", etc.
    title: Optional[str] = None
    value: Optional[str] = None
    description: Optional[str] = None
    position: Optional[Tuple[int, int]] = None   # (x, y) screen coords
    size: Optional[Tuple[int, int]] = None       # (width, height)
    enabled: bool = True
    focused: bool = False
    children: List["AXElement"] = field(default_factory=list)
    raw_ref: Any = None

    @property
    def center(self) -> Optional[Tuple[int, int]]:
        if self.position and self.size:
            return (
                self.position[0] + self.size[0] // 2,
                self.position[1] + self.size[1] // 2,
            )
        return None


class AccessibilityBridge:
    """
    Bridge to macOS Accessibility API.

    Provides:
    - UI element tree traversal
    - Element search by role/title/description
    - Element position lookup (pixel-accurate, logical coordinates)
    - Frontmost app/window state

    Requires: Accessibility permission in System Settings.
    """

    def __init__(self, max_depth: int = 8):
        self.max_depth = max_depth

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

    def get_element_tree(self, max_depth: Optional[int] = None) -> Optional[AXElement]:
        """Get the full accessibility tree of the focused window."""
        ...

    def find_elements(
        self,
        role: Optional[str] = None,
        title_contains: Optional[str] = None,
        enabled_only: bool = True,
    ) -> List[AXElement]:
        """Find elements matching criteria in the focused window."""
        ...

    def find_element_by_description(self, description: str) -> Optional[AXElement]:
        """
        Find element matching a natural language description.
        Maps keywords to AX roles:
        - "search button" --> role=AXButton, title_contains="search"
        - "text field"    --> role=AXTextField
        """
        ...

    def get_interactive_elements(self) -> List[AXElement]:
        """Get all clickable/typeable elements with positions."""
        ...

    def serialize_tree(self) -> str:
        """
        Serialize element tree to compact text for LLM context.
        Format:
            [Button "Submit" @ (450, 320) 120x40]
            [TextField "Search" value="query" @ (100, 50) 300x30]
        """
        ...
```

#### Integration into VisionCoordinator

The VisionCoordinator's `find_element` method gets an accessibility fast-path:

```python
# In vision_coordinator.py

async def find_element(self, description: str) -> Optional[dict]:
    """
    Find a UI element. Strategy:
    1. Try accessibility API first (instant, accurate)
    2. Fall back to vision model (slower, less accurate)
    """
    # FAST PATH: Accessibility API
    if self.accessibility:
        ax_element = self.accessibility.find_element_by_description(description)
        if ax_element and ax_element.center:
            cx, cy = ax_element.center
            return {"x": cx, "y": cy, "source": "accessibility"}

    # SLOW PATH: Vision model (existing code)
    return await self._find_element_vision(description)
```

#### Coordinate Space Consideration

**Important:** Accessibility API returns coordinates in **screen logical space** (same as PyAutoGUI). These do NOT need Retina scaling. Vision model returns coordinates relative to the screenshot image, which DO need scaling.

Track the source:
```python
# In find_element return dict:
{"x": 450, "y": 320, "source": "accessibility"}  # Already logical
{"x": 900, "y": 640, "source": "vision"}          # Needs Retina scaling
```

The actuator should check `source` and only scale vision-sourced coordinates.

### 3.3 Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/automation_agent/perception/accessibility.py` | **CREATE** | AccessibilityBridge class |
| `src/automation_agent/orchestrator/vision_coordinator.py` | **MODIFY** | Add accessibility fast-path |
| `src/automation_agent/config.py` | **MODIFY** | Add `use_accessibility` setting |
| `tests/perception/test_accessibility.py` | **CREATE** | Unit tests |

---

## 4. Phase 2: Grounding Model Upgrade (Week 1-2)

### 4.1 Problem

We use vanilla Qwen2-VL / Qwen3-VL prompted to return bounding box coordinates. This general VLM was not trained for UI grounding. Coordinate format inference is fragile.

### 4.2 Design

Replace the vision grounding backend with **UGround-7B** (same Qwen2-VL backbone, fine-tuned on 10M UI elements from 1.3M screenshots).

#### Option A: UGround via OpenAI-compatible Server (Preferred)

Since we already support any OpenAI-compatible vision endpoint via `vision_server_url`, we can serve UGround through llama.cpp, vLLM, or SGLang:

```bash
# Serve UGround-7B via vLLM
python -m vllm.entrypoints.openai.api_server \
    --model osu-nlp-group/UGround-V1-7B \
    --port 8080

# Config
AGENT_VISION_SERVER_URL=http://localhost:8080
AGENT_VISION_MODEL=osu-nlp-group/UGround-V1-7B
```

No code changes needed for basic integration — just a model swap.

#### Option B: Dedicated Grounding Client

For more control, add a separate grounding model that the VisionCoordinator can call specifically for element location (while keeping the general VLM for screen description):

```python
# In config.py:
grounding_model: str = Field(
    default="",  # Empty = use vision_model for grounding too
    description="Specialized grounding model. If set, used for find_element calls.",
)
grounding_server_url: str = Field(
    default="",  # Empty = use vision_server_url
    description="Server URL for grounding model (if different from vision server)",
)
```

```python
# In vision_coordinator.py:
async def find_element(self, description: str) -> Optional[dict]:
    # 1. Accessibility (Phase 1)
    # 2. Dedicated grounding model (if configured)
    if self.grounding_client:
        return await self._find_element_grounding(description)
    # 3. General vision model (existing)
    return await self._find_element_vision(description)
```

### 4.3 Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/automation_agent/config.py` | **MODIFY** | Add `grounding_model`, `grounding_server_url` |
| `src/automation_agent/orchestrator/vision_coordinator.py` | **MODIFY** | Add grounding model path |

---

## 5. Phase 3: Screenshot-Diff Action Verification (Week 2)

### 5.1 Problem

Our verifier uses vision-based verification (asking the VLM if the step's verify condition is met). This is good but expensive (full VLM call). We lack a cheap, fast check: "did the screen change at all after I clicked?"

VyUI has real-time error detection. Agent S2 has hierarchical replanning. We have `retry_different` and `replan`, but we don't detect failed clicks quickly enough.

### 5.2 Design

Add a `ScreenshotDiffVerifier` that captures before/after screenshots and checks for pixel changes. This is a ~50ms check that can trigger immediate retry without waiting for a full vision verification.

#### New File: `orchestrator/screenshot_diff.py`

```python
"""Fast action verification via screenshot comparison."""

import numpy as np
from typing import Optional
from ..perception.capture import ScreenCapturer


class ScreenshotDiffVerifier:
    """
    Verifies that actions had visible effect by comparing screenshots.

    Strategies:
    1. Full-screen diff: did anything change?
    2. Region diff: did the click target area change?
    """

    def __init__(self, capturer: ScreenCapturer, change_threshold: float = 0.01):
        self.capturer = capturer
        self.change_threshold = change_threshold
        self._before: Optional[np.ndarray] = None

    def capture_before(self) -> None:
        """Capture screenshot before action."""
        img = self.capturer.capture_screen()
        self._before = np.array(img)

    def screen_changed(self) -> bool:
        """Check if screen changed since capture_before()."""
        if self._before is None:
            return True
        after = np.array(self.capturer.capture_screen())
        if self._before.shape != after.shape:
            return True
        diff = np.abs(self._before.astype(float) - after.astype(float))
        return float(np.mean(diff > 10)) > self.change_threshold

    def region_changed(self, x: int, y: int, radius: int = 100) -> bool:
        """Check if region around (x,y) changed."""
        if self._before is None:
            return True
        after = np.array(self.capturer.capture_screen())
        if self._before.shape != after.shape:
            return True
        h, w = self._before.shape[:2]
        x1, y1 = max(0, x - radius), max(0, y - radius)
        x2, y2 = min(w, x + radius), min(h, y + radius)
        pre_region = self._before[y1:y2, x1:x2]
        post_region = after[y1:y2, x1:x2]
        diff = np.abs(pre_region.astype(float) - post_region.astype(float))
        return float(np.mean(diff > 10)) > self.change_threshold
```

#### Integration into Agent

In `agent.py`, wrap `_dispatch_action` with before/after screenshot:

```python
# Before dispatching click actions:
if self.screenshot_diff and step.action == "click":
    self.screenshot_diff.capture_before()

actuator_result = await self._dispatch_action(step)

# After click, quick diff check:
if self.screenshot_diff and step.action == "click" and actuator_result.get("success"):
    await asyncio.sleep(0.3)  # Brief wait for UI update
    if not self.screenshot_diff.region_changed(
        actuator_result.get("x", 0), actuator_result.get("y", 0)
    ):
        # Click had no visible effect — mark as failed for retry
        actuator_result["success"] = False
        actuator_result["error"] = "Click had no visible effect (screenshot unchanged)"
```

This triggers the existing `retry_different` mechanism automatically.

### 5.3 Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/automation_agent/orchestrator/screenshot_diff.py` | **CREATE** | ScreenshotDiffVerifier |
| `src/automation_agent/orchestrator/agent.py` | **MODIFY** | Add screenshot diff before/after clicks |
| `tests/orchestrator/test_screenshot_diff.py` | **CREATE** | Tests |

---

## 6. Phase 4: Context Monitor (Week 2-3)

### 6.1 Problem

Each observation takes a full VLM screenshot call (3-7s). We don't track what app/window is focused between steps, form progress, or navigation history. The planner gets only step-level history.

VyUI's Context Monitor continuously tracks window states and selected elements.

### 6.2 Design

Add a `ContextMonitor` that maintains running desktop state, updated cheaply via the Accessibility API.

#### New File: `orchestrator/context_monitor.py`

```python
"""Persistent context monitor for desktop state tracking."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from ..perception.accessibility import AccessibilityBridge, AXElement


@dataclass
class DesktopContext:
    """Full snapshot of the desktop state."""
    frontmost_app: str = ""
    window_title: str = ""
    interactive_elements: List[AXElement] = field(default_factory=list)
    recently_clicked: Optional[str] = None
    recently_typed: Optional[str] = None
    form_fields_filled: Dict[str, str] = field(default_factory=dict)
    navigation_history: List[str] = field(default_factory=list)
    last_vision_description: str = ""
    iteration_count: int = 0


class ContextMonitor:
    """
    Maintains persistent desktop context across agent iterations.

    - Cheap updates via Accessibility API (~50ms, no VLM call)
    - Full vision refresh only when context is stale
    - Provides structured context to the planner
    """

    def __init__(self, accessibility: Optional[AccessibilityBridge] = None):
        self.accessibility = accessibility
        self.context = DesktopContext()

    def update_cheap(self) -> DesktopContext:
        """Update context via Accessibility API (~50ms)."""
        if self.accessibility:
            app_info = self.accessibility.get_frontmost_app()
            if app_info:
                self.context.frontmost_app = app_info["name"]
            window = self.accessibility.get_focused_window()
            if window:
                self.context.window_title = window.title or ""
                self.context.interactive_elements = (
                    self.accessibility.get_interactive_elements()
                )
        self.context.iteration_count += 1
        return self.context

    def needs_full_vision(self) -> bool:
        """Should we do a full VLM observation?"""
        if not self.context.last_vision_description:
            return True
        if self.context.iteration_count % 3 == 0:
            return True  # Refresh every 3 steps
        return False

    def record_click(self, target: str):
        self.context.recently_clicked = target

    def record_type(self, text: str, field_name: Optional[str] = None):
        self.context.recently_typed = text
        if field_name:
            self.context.form_fields_filled[field_name] = text

    def record_navigation(self, url: str):
        self.context.navigation_history.append(url)

    def format_for_planner(self) -> str:
        """Format context as structured text for planner prompt."""
        lines = [f"## Desktop State"]
        lines.append(f"App: {self.context.frontmost_app}")
        lines.append(f"Window: {self.context.window_title}")
        if self.context.interactive_elements:
            lines.append(f"Interactive elements ({len(self.context.interactive_elements)}):")
            for elem in self.context.interactive_elements[:20]:
                label = elem.title or elem.description or elem.role
                lines.append(f"  - [{elem.role.replace('AX','')}] {label}")
        if self.context.last_vision_description:
            lines.append(f"\n## Screen: {self.context.last_vision_description[:500]}")
        if self.context.form_fields_filled:
            lines.append(f"\n## Form Progress")
            for k, v in self.context.form_fields_filled.items():
                lines.append(f"  {k}: {v}")
        return "\n".join(lines)
```

#### Integration into Agent

The agentic execute loop adds cheap context updates:

```python
# Before each step:
if self.context_monitor:
    self.context_monitor.update_cheap()

# Only do full vision when needed:
if not self.context_monitor or self.context_monitor.needs_full_vision():
    screen_desc = await self.coordinator.describe_screen()
    if self.context_monitor:
        self.context_monitor.context.last_vision_description = screen_desc

# After actions, record context:
if self.context_monitor:
    if step.action == "click":
        self.context_monitor.record_click(step.params.get("element", ""))
    elif step.action == "type_text":
        self.context_monitor.record_type(step.params.get("text", ""))
    elif step.action == "open_url":
        self.context_monitor.record_navigation(step.params.get("url", ""))
```

### 6.3 Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/automation_agent/orchestrator/context_monitor.py` | **CREATE** | ContextMonitor |
| `src/automation_agent/orchestrator/agent.py` | **MODIFY** | Integrate context updates |
| `tests/orchestrator/test_context_monitor.py` | **CREATE** | Tests |

---

## 7. Phase 5: Mixture-of-Grounding Router (Week 3-4)

### 7.1 Problem

Even with accessibility + UGround, no single strategy works for all elements:
- Accessibility: great for standard widgets, fails for web canvas content
- Vision: great for visual elements, fails for identical-looking buttons
- OCR: great for finding text, fails for icons

Agent S2 proved that **routing to the right expert** is the key insight.

### 7.2 Design

Add a `GroundingRouter` that picks the best strategy per element type.

#### New File: `orchestrator/grounding_router.py`

```python
"""Mixture-of-Grounding: route element finding to the best expert."""

from enum import Enum
from dataclasses import dataclass
from typing import Any, Optional

from ..perception.accessibility import AccessibilityBridge


class GroundingStrategy(Enum):
    ACCESSIBILITY = "accessibility"
    VISION = "vision"
    OCR = "ocr"


@dataclass
class GroundingResult:
    x: int
    y: int
    strategy_used: GroundingStrategy
    confidence: float
    element_info: Optional[dict] = None


class GroundingRouter:
    """
    Routes element-finding to the best grounding expert.
    Inspired by Agent S2's Mixture-of-Grounding (MoG).
    """

    def __init__(self, accessibility=None, vision_coordinator=None):
        self.accessibility = accessibility
        self.vision = vision_coordinator

    def classify(self, description: str) -> list[GroundingStrategy]:
        """Return ordered strategies to try for this element."""
        desc = description.lower()

        # Standard widgets --> Accessibility first
        if any(kw in desc for kw in [
            "button", "text field", "input", "checkbox", "radio",
            "dropdown", "menu", "tab", "slider", "link", "submit",
        ]):
            return [GroundingStrategy.ACCESSIBILITY, GroundingStrategy.VISION]

        # Text content --> OCR first
        if any(kw in desc for kw in [
            "text that says", "label", "heading", "price", "number",
        ]):
            return [GroundingStrategy.OCR, GroundingStrategy.VISION]

        # Visual elements --> Vision first
        if any(kw in desc for kw in [
            "image", "icon", "thumbnail", "video", "logo", "chart",
        ]):
            return [GroundingStrategy.VISION, GroundingStrategy.ACCESSIBILITY]

        # Position-based --> Vision (needs spatial reasoning)
        if any(kw in desc for kw in [
            "first", "second", "top", "bottom", "closest", "most popular",
        ]):
            return [GroundingStrategy.VISION, GroundingStrategy.ACCESSIBILITY]

        # Default
        return [GroundingStrategy.ACCESSIBILITY, GroundingStrategy.VISION]

    async def find_element(self, description: str) -> Optional[GroundingResult]:
        """Find element using best available strategy with fallback."""
        for strategy in self.classify(description):
            result = await self._try_strategy(strategy, description)
            if result:
                return result
        return None

    async def _try_strategy(self, strategy, description) -> Optional[GroundingResult]:
        if strategy == GroundingStrategy.ACCESSIBILITY:
            return await self._ground_accessibility(description)
        elif strategy == GroundingStrategy.VISION:
            return await self._ground_vision(description)
        elif strategy == GroundingStrategy.OCR:
            return await self._ground_ocr(description)
        return None

    async def _ground_accessibility(self, description) -> Optional[GroundingResult]:
        if not self.accessibility:
            return None
        elem = self.accessibility.find_element_by_description(description)
        if not elem or not elem.center:
            return None
        return GroundingResult(
            x=elem.center[0], y=elem.center[1],
            strategy_used=GroundingStrategy.ACCESSIBILITY,
            confidence=0.95,
            element_info={"role": elem.role, "title": elem.title},
        )

    async def _ground_vision(self, description) -> Optional[GroundingResult]:
        if not self.vision:
            return None
        location = await self.vision.find_element(description)
        if not location:
            return None
        return GroundingResult(
            x=location["x"], y=location["y"],
            strategy_used=GroundingStrategy.VISION,
            confidence=0.75,
        )

    async def _ground_ocr(self, description) -> Optional[GroundingResult]:
        """Find element via OCR text matching using pytesseract."""
        # Extract target text, run pytesseract, find best match
        ...
```

### 7.3 Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `src/automation_agent/orchestrator/grounding_router.py` | **CREATE** | GroundingRouter |
| `src/automation_agent/orchestrator/agent.py` | **MODIFY** | Use router instead of direct vision calls |
| `tests/orchestrator/test_grounding_router.py` | **CREATE** | Tests |

---

## 8. Phase 6: Enhanced Planning with Structured Context (Week 3-4)

### 8.1 Problem

Our planner gets a screen description and goal. It doesn't see the accessibility element inventory or form progress. The LLM must infer what elements exist from prose descriptions.

### 8.2 Design

Enhance the planner prompt with structured context from the ContextMonitor:

**Before (vision description only):**
```
"I see a web page with several buttons and a search bar..."
```

**After (structured context):**
```
## Desktop State
App: Safari
Window: OpenTable - Book restaurants

Interactive elements (12):
  - [Button] "Find a Table"
  - [TextField] "Restaurant or Cuisine" value="sushi"
  - [PopUpButton] "Party Size" value="2 people"
  - [TextField] "Date" value="Today"
  - [TextField] "Time" value="7:00 PM"
  - [Button] "Let's go"

## Form Progress
  Restaurant: sushi
  Party Size: 2
```

The planner can now reference elements by exact name and knows current form state without re-observing.

#### Integration

In `planner.py`, add context to the planning prompt:

```python
async def plan(self, goal, screen_description="", skill_context=None, desktop_context=""):
    prompt = f"Goal: {goal}\n"
    if desktop_context:
        prompt += f"\n{desktop_context}\n"
    if screen_description:
        prompt += f"\nScreen: {screen_description}\n"
    ...
```

---

## 9. Implementation Sequence & Dependencies

```
Week 1 (parallel):
+---------------------+     +----------------------+
| Phase 1:            |     | Phase 2:             |
| Accessibility Bridge|     | Grounding Model      |
| (perception/        |     | Upgrade              |
|  accessibility.py)  |     | (config change +     |
+---------------------+     |  model swap)         |
          |                  +----------------------+
          v
Week 2 (parallel):
+---------------------+     +----------------------+
| Phase 3:            |     | Phase 4:             |
| Screenshot Diff     |     | Context Monitor      |
| (orchestrator/      |     | (orchestrator/       |
|  screenshot_diff.py)|     |  context_monitor.py) |
+---------------------+     +----------------------+
          |                           |
          +----------+----------------+
                     v
Week 3-4:
+--------------------------------------+
| Phase 5: Grounding Router            |
| + Phase 6: Enhanced Planning         |
+--------------------------------------+
```

### Dependencies

| Phase | Depends On | Can Start After |
|-------|-----------|-----------------|
| Phase 1 (Accessibility) | Nothing | Immediately |
| Phase 2 (Grounding Model) | Nothing | Immediately (parallel) |
| Phase 3 (Screenshot Diff) | Nothing | Immediately (parallel) |
| Phase 4 (Context Monitor) | Phase 1 | Phase 1 complete |
| Phase 5 (Grounding Router) | Phases 1 + 2 | Phases 1 & 2 complete |
| Phase 6 (Enhanced Planning) | Phase 4 | Phase 4 complete |

**Phases 1, 2, and 3 can be developed in parallel.**

---

## 10. New File Inventory

| File | Phase | Lines (est.) | Purpose |
|------|-------|-------------|---------|
| `src/automation_agent/perception/accessibility.py` | 1 | ~300 | macOS Accessibility API bridge |
| `src/automation_agent/orchestrator/screenshot_diff.py` | 3 | ~80 | Screenshot diff verifier |
| `src/automation_agent/orchestrator/context_monitor.py` | 4 | ~150 | Persistent context monitor |
| `src/automation_agent/orchestrator/grounding_router.py` | 5 | ~200 | Mixture-of-Grounding router |
| `tests/perception/test_accessibility.py` | 1 | ~100 | Accessibility tests |
| `tests/orchestrator/test_screenshot_diff.py` | 3 | ~80 | Diff verifier tests |
| `tests/orchestrator/test_context_monitor.py` | 4 | ~100 | Context monitor tests |
| `tests/orchestrator/test_grounding_router.py` | 5 | ~100 | Router tests |
| **Total new** | | **~1,110** | |

### Modified Files

| File | Phases | Changes |
|------|--------|---------|
| `src/automation_agent/config.py` | 1,2 | Add `use_accessibility`, `grounding_model` settings |
| `src/automation_agent/orchestrator/vision_coordinator.py` | 1,2 | Accessibility fast-path, grounding model path |
| `src/automation_agent/orchestrator/agent.py` | 3,4,5 | Screenshot diff, context updates, grounding router |
| `src/automation_agent/orchestrator/planner.py` | 6 | Accept desktop context in prompt |

---

## 11. Backward Compatibility & Feature Flags

All new features are **opt-in** and **additive**:

```bash
# Use everything (recommended)
AGENT_USE_ACCESSIBILITY=true
AGENT_GROUNDING_MODEL=uground-7b
AGENT_USE_SCREENSHOT_DIFF=true
AGENT_USE_CONTEXT_MONITOR=true

# Use only accessibility (fastest improvement, no model changes)
AGENT_USE_ACCESSIBILITY=true

# Use only grounding model upgrade (best accuracy improvement)
AGENT_GROUNDING_MODEL=uground-7b

# Legacy mode (current behavior, no changes)
# (just don't set any of the above)
```

Each feature gracefully falls back when unavailable:
- No accessibility permission? Falls back to vision.
- UGround not available? Falls back to default vision model.
- Screenshot diff disabled? Existing verifier handles it.
- No context monitor? Planner works as before.

---

## 12. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Accessibility permission not granted | Medium | High | Graceful fallback; clear error message |
| UGround not available as served model | Medium | Medium | Support HuggingFace fallback; Qwen2-VL still works |
| Web content has limited AX data | High | Medium | Vision handles this; AX is a fast-path, not required |
| Screenshot diff false positives (animations) | Medium | Low | Configurable threshold; region-based diffing |
| Multiple experts add latency | Medium | Medium | Route to ONE expert first; fallback only on failure |
| pyobjc AX API is complex/fragile | Medium | Medium | Wrap in try/except; fall back on any error |

---

## 13. Success Criteria

### Phase 1: Accessibility Bridge
- [ ] Can enumerate interactive elements in Safari
- [ ] Can find buttons by title
- [ ] VisionCoordinator uses accessibility as fast-path
- [ ] Coordinates work correctly (no Retina double-scaling)
- [ ] All existing tests still pass

### Phase 2: Grounding Model
- [ ] UGround model served and accessible
- [ ] VisionCoordinator routes grounding to UGround
- [ ] Grounding accuracy measurably improved on 10 test screenshots

### Phase 3: Screenshot Diff
- [ ] Detects when clicks have no visible effect
- [ ] Triggers retry_different mechanism on failed clicks
- [ ] Adds <500ms latency per action

### Phase 4: Context Monitor
- [ ] Tracks frontmost app and window title
- [ ] Cheap updates take <100ms
- [ ] Full vision refreshes only when needed
- [ ] Planner receives structured element context

### Phase 5: Grounding Router
- [ ] Correctly classifies element types
- [ ] Tries multiple strategies in order
- [ ] End-to-end task completion improved

### Phase 6: Enhanced Planning
- [ ] Planner sees element inventory from accessibility
- [ ] Form filling tasks complete with fewer iterations
