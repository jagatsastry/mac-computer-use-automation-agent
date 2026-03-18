# Bug and Fix Log

This document tracks bugs discovered during development and their fixes.

---

## Bug #001: Retina Display Coordinate Mismatch

**Date:** 2026-02-02

**Severity:** Critical

**Symptom:**
Clicks were landing in incorrect locations on the screen. For example, when trying to click a time slot button on OpenTable, the click would land far away from the intended target.

**Investigation:**
1. Drew bounding boxes at coordinates Claude reported for a UI element
2. Discovered the visual bounding box was in a completely different location than expected
3. Compared PyAutoGUI screen size with screenshot dimensions:
   - PyAutoGUI logical screen: 1512 x 982
   - Screenshot actual pixels: 3024 x 1964
   - Ratio: 2.0x (Retina display scaling)

**Root Cause:**
On macOS Retina displays, `pyautogui.screenshot()` captures at the native resolution (e.g., 3024x1964) which is 2x the logical screen resolution (1512x982). When Claude's vision model analyzes the screenshot, it reports coordinates in the screenshot's pixel space. However, `pyautogui.click(x, y)` expects coordinates in the logical screen space. This 2x mismatch caused clicks to land at roughly half the distance from the origin they should have been.

**Example:**
- Claude reports: Click at (1806, 612) for a time slot button
- Without fix: PyAutoGUI clicks at (1806, 612) logical = way off screen or wrong location
- With fix: PyAutoGUI clicks at (903, 306) logical = correct location

**Fix Applied:**
Modified `src/automation_agent/actions/simple.py` - `ClickAction` class:

```python
class ClickAction(ActionBase):
    """Click at coordinates with Retina display scaling support."""

    # Class-level scaling factor cache
    _scale_factor: float = None

    @classmethod
    def _get_scale_factor(cls) -> float:
        """Calculate scaling factor between screenshot and logical pixels."""
        if cls._scale_factor is None:
            logical_width, logical_height = pyautogui.size()
            screenshot = pyautogui.screenshot()
            screenshot_width, screenshot_height = screenshot.size
            cls._scale_factor = screenshot_width / logical_width
        return cls._scale_factor

    def _scale_coordinates(self) -> Tuple[int, int]:
        """Scale coordinates from screenshot space to logical screen space."""
        scale = self._get_scale_factor()
        scaled_x = int(self.x / scale)
        scaled_y = int(self.y / scale)
        return scaled_x, scaled_y

    async def execute(self) -> ActionResult:
        scaled_x, scaled_y = self._scale_coordinates()
        pyautogui.click(scaled_x, scaled_y)
        # ... metadata includes original, scaled, and scale_factor
```

**Files Modified:**
- `src/automation_agent/actions/simple.py`

**Tests Added:**
- `tests/test_click_scaling.py` - Unit tests for coordinate scaling logic

**Verification:**
- Created visual verification script `verify_click_fix.py`
- Confirmed scale factor detection works (2.0 on Retina)
- Confirmed coordinate transformation is correct

**Prevention:**
- Added unit tests to prevent regression
- Scale factor is cached at class level for performance
- Metadata in ActionResult includes both original and scaled coordinates for debugging

**Follow-up Fix (Bug #001b):**
The original fix in ClickAction assumed ALL coordinates come from screenshot space. However, the observer's `_get_screen_size()` was returning LOGICAL screen size, causing double-scaling when coordinates went through both observer and ClickAction.

**Additional Fix:**
Updated `observer.py` `_get_screen_size()` to return screenshot dimensions instead of logical dimensions:
```python
def _get_screen_size(self) -> Tuple[int, int]:
    # Get screenshot dimensions (what Claude actually sees)
    screenshot = self.capturer.capture_screen()
    self._screen_size = screenshot.size  # Returns (3024, 1964) on Retina
```

This ensures the entire coordinate pipeline is consistent:
1. Claude sees screenshot at 3024x1964
2. Claude returns normalized 0-1000 coords
3. Observer converts to screenshot pixels using 3024x1964
4. ClickAction scales from screenshot to logical using 2.0x factor

---

## Bug #002: Time Slot Selection Not Specific to Requested Time

**Date:** 2026-02-02

**Severity:** Medium

**Symptom:**
When booking restaurant reservations, the agent would click on any available time slot rather than the one closest to the user's requested time (e.g., user asks for "7pm" but agent clicks "5:30pm").

**Root Cause:**
1. The AGENT_PLANNER_SYSTEM_PROMPT didn't guide the planner to include specific times in click_element descriptions
2. The ELEMENT_FINDER_SYSTEM_PROMPT didn't have logic for finding the "closest" time slot

**Fix Applied:**

1. Updated `src/automation_agent/orchestrator/agent.py` - Added Section 7 to AGENT_PLANNER_SYSTEM_PROMPT:
```python
### 7. Time Slot Selection (Restaurant Reservations, Appointments)
When selecting time slots:
- ALWAYS include the specific time in your click_element description
- Reference the time from the user's original goal
- Be specific: "time slot closest to 7:00 PM" NOT just "a time slot"
```

2. Updated `src/automation_agent/orchestrator/observer.py` - Added time slot handling to ELEMENT_FINDER_SYSTEM_PROMPT:
```python
### Time Slots
When asked to find a time slot closest to a specific time:
1. Look at ALL available time slots on the screen
2. Find the one CLOSEST to the requested time
3. If exact time not available, prefer the next available time AFTER
4. Return the bounding box of that specific time slot button
```

**Files Modified:**
- `src/automation_agent/orchestrator/agent.py`
- `src/automation_agent/orchestrator/observer.py`

**Tests Added:**
- None (prompt engineering change - tested manually)

**Verification:**
- Re-run restaurant booking test with specific time request

---

## Template for Future Bugs

### Bug #XXX: [Brief Title]

**Date:** YYYY-MM-DD

**Severity:** Critical / High / Medium / Low

**Symptom:**
[What the user/developer observed]

**Investigation:**
[Steps taken to investigate]

**Root Cause:**
[Technical explanation of why the bug occurred]

**Fix Applied:**
[Description of the fix with code snippets if relevant]

**Files Modified:**
- [List of files]

**Tests Added:**
- [List of test files/functions]

**Verification:**
[How the fix was verified]

**Prevention:**
[How to prevent this bug from recurring]
