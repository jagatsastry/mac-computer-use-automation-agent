# Vision-Based Automation - Ready to Test

**Status:** LLaVA model downloading (40% complete, ~3 min remaining)
**Next:** Test Safari icon detection and clicking

## What's Ready

### 1. Component Test Infrastructure ✅
- 6 component test scripts
- 5/6 tests passing (vision pending model)
- Real implementations (no mocks)
- Automated test runner

### 2. Vision Coordinate Tests ✅
Created two specialized vision tests:

**test_vision_coordinates.py**
- Comprehensive coordinate detection
- Tests multiple UI elements
- Visual bounding boxes
- Side-by-side comparisons

**test_safari_click.py**
- End-to-end Safari automation
- Vision model detection
- Coordinate parsing
- Visual annotation
- Actual clicking

### 3. Enhanced Components ✅

**OllamaClient**
- `generate_vision()` - Vision model with images
- `generate_stream()` - Streaming generation
- `list_models()` - Model listing
- Support for image_b64 input

**ScreenCapturer**
- `capture_screen()` - PIL Image capture
- `capture_region()` - Region capture
- `capture_screen_b64()` - Base64 encoding

**Visualization**
- `parse_bbox()` - Parse model bounding boxes
- `draw_bbox()` - Draw rectangles and labels
- Crosshair marking for click points

## Test Workflow

### Phase 1: Identify Safari (Ready to test)
```bash
# Dry run (visualize only)
python scripts/test_safari_click.py

# Actually click Safari
python scripts/test_safari_click.py --execute
```

**What it does:**
1. Captures screenshot
2. Sends to vision model: "Find Safari icon"
3. Parses response: `<box>(145,892,198,945)</box>`
4. Converts normalized (0-1000) to pixel coordinates
5. Draws bounding box and crosshair on image
6. Calculates center point for clicking
7. Executes click (if --execute flag)

**Expected output:**
- Original screenshot
- Annotated screenshot with bounding box
- Console output with coordinates
- Safari launches (if --execute)

### Phase 2: Find URL Bar (After Safari opens)
```bash
# Modify test_safari_click.py or create new test
python scripts/test_url_bar.py
```

**Workflow:**
1. Wait for Safari to finish opening (~2 seconds)
2. Capture new screenshot
3. Ask vision model: "Find Safari URL address bar"
4. Parse coordinates
5. Click on URL bar
6. Verify URL bar is focused

### Phase 3: Type URL (After URL bar focused)
```python
# Type URL
type_action = TypeAction(text="https://google.com")
await type_action.execute()

# Press Enter
hotkey_action = HotkeyAction("return")
await hotkey_action.execute()
```

### Phase 4: Full Automation (Complete workflow)
Combine all steps into single script:
```bash
python scripts/test_full_automation.py --task "open google.com in safari"
```

## Vision Model Configuration

**Current:** LLaVA (4.1GB) - downloading
**Alternative:** llama3.2-vision (better accuracy, 7GB)
**Alternative:** qwen2.5vl (specialized for bounding boxes, 8GB)

### Change Model
```bash
# In .env file
AGENT_VISION_MODEL=llava

# Or environment variable
export AGENT_VISION_MODEL=llama3.2-vision
```

### Model Capabilities

**LLaVA** (recommended for testing)
- ✅ Fast inference
- ✅ Smaller model (4.1GB)
- ✅ Good UI element detection
- ⚠️  May not support `<box>` format natively

**llama3.2-vision**
- ✅ Better accuracy
- ✅ More reliable detection
- ✅ Better reasoning
- ❌ Larger (7GB)
- ❌ Slower inference

**qwen2.5vl**
- ✅ Native bounding box support
- ✅ Specialized for vision tasks
- ✅ Best coordinate accuracy
- ❌ Largest (8GB)

## Coordinate System

### Normalized Coordinates (0-1000)
Vision models return coordinates in normalized format:
```
<box>(x1,y1,x2,y2)</box>
where 0 <= x1,y1,x2,y2 <= 1000
```

### Pixel Coordinates
Convert to actual screen pixels:
```python
screen_width = 3024  # Example
screen_height = 1964

pixel_x1 = (norm_x1 / 1000.0) * screen_width
pixel_y1 = (norm_y1 / 1000.0) * screen_height
```

### Click Point
Use center of bounding box:
```python
center_x = (pixel_x1 + pixel_x2) // 2
center_y = (pixel_y1 + pixel_y2) // 2
```

## Visualization Format

The annotated images show:
- **Red rectangle**: Bounding box from vision model
- **Red crosshair**: Click point (center)
- **Label**: Element name + coordinates
- **Original image**: Side-by-side comparison

Example annotation:
```
┌─────────────────────────┐
│ Safari (click: 518,1804)│  ← Label
├─────────────────────────┤
│                         │
│          ╳              │  ← Crosshair at click point
│                         │
└─────────────────────────┘
```

## Testing Strategy

### 1. Dry Run First (Always)
```bash
python scripts/test_safari_click.py
```
- Verifies detection works
- Shows coordinates visually
- No risk of unwanted clicks

### 2. Review Annotated Image
```bash
open scripts/test_components/output/safari_annotated_*.png
```
- Check bounding box accuracy
- Verify click point location
- Ensure it's the right element

### 3. Execute if Correct
```bash
python scripts/test_safari_click.py --execute
```
- Actually performs the click
- Monitors for success
- Captures any errors

### 4. Iterate if Needed
- Adjust prompts for better detection
- Try different vision models
- Fine-tune coordinate parsing

## Error Handling

### Model Response Issues

**No bounding box found:**
```
⚠️ Could not find bounding box in response
```
**Solutions:**
- Try more specific prompt
- Use different vision model
- Verify element is visible

**Multiple bounding boxes:**
- Parse all boxes: `re.findall(pattern, response)`
- Select most likely match (size, position)
- Ask for clarification in prompt

**Coordinates out of bounds:**
```
❌ Click coordinates out of bounds!
```
**Solutions:**
- Check coordinate conversion
- Verify screen size
- Ensure element on main display

### Click Execution Issues

**Permission denied:**
- Grant Accessibility permission
- System Settings → Privacy & Security → Accessibility

**Wrong element clicked:**
- Review annotated image
- Adjust bounding box parsing
- Try center vs other points (top-left, etc.)

**Element not found:**
- Verify element visible
- Check screen hasn't changed
- Add retries with screenshots

## Next Tests to Create

### 1. test_url_bar.py
```python
async def find_and_click_url_bar():
    # Assume Safari is open
    # Find URL bar
    # Click it
    # Verify focus
```

### 2. test_type_url.py
```python
async def type_and_navigate(url):
    # Assume URL bar is focused
    # Clear existing text
    # Type new URL
    # Press Enter
```

### 3. test_full_workflow.py
```python
async def open_website(url):
    # Find and click Safari
    # Wait for Safari to open
    # Find and click URL bar
    # Type URL
    # Press Enter
    # Wait for page load
```

### 4. test_error_recovery.py
```python
async def test_recovery():
    # Test: What if Safari already open?
    # Test: What if URL bar has text?
    # Test: What if Safari not visible?
    # Implement retry logic
```

## Files Created

**Test Scripts:**
- `scripts/test_safari_click.py` - Safari automation
- `scripts/test_components/test_vision_coordinates.py` - Comprehensive test

**Documentation:**
- `scripts/SAFARI_CLICK_TEST.md` - Usage guide
- `VISION_AUTOMATION_READY.md` - This file

**Outputs:**
- `output/safari_original_*.png` - Original screenshots
- `output/safari_annotated_*.png` - With bounding boxes
- `output/comparison_*.png` - Side-by-side views

## Ready to Run

Once LLaVA finishes downloading:

```bash
# Check model is available
ollama list | grep llava

# Test vision coordinate detection
cd /Users/jagatp/workspace/macos-automation-agent
source venv/bin/activate

# Dry run (safe)
python scripts/test_safari_click.py

# Review output
open scripts/test_components/output/safari_annotated_*.png

# Execute if looks good
python scripts/test_safari_click.py --execute
```

## Architecture

```
User Request: "Open Safari"
    ↓
[1] Screen Capture
    → screenshot.png (3024x1964)
    ↓
[2] Vision Model
    → "Find Safari icon"
    → <box>(145,892,198,945)</box>
    ↓
[3] Coordinate Parser
    → Normalized: (145,892,198,945)
    → Pixels: (438,1752,598,1856)
    → Center: (518,1804)
    ↓
[4] Visualization
    → Draw bounding box
    → Mark click point
    → Save annotated image
    ↓
[5] Action Execution
    → ClickAction(518, 1804)
    → Safari launches ✅
```

---

**Status:** Ready to test once LLaVA downloads
**ETA:** ~2-3 minutes
**Next Command:** `python scripts/test_safari_click.py`
