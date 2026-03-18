# Safari Click Test - Vision-Based Automation

This test demonstrates end-to-end vision-based automation:
1. Capture screenshot
2. Use vision model to identify Safari icon
3. Parse bounding box coordinates
4. Visualize detection with annotated image
5. Click on Safari to launch it

## Scripts

### test_safari_click.py
Complete workflow to find and click Safari.

```bash
# Dry run (visualize only, no clicking)
python scripts/test_safari_click.py

# Actually click Safari
python scripts/test_safari_click.py --execute
```

**Output:**
- `safari_original_{timestamp}.png` - Original screenshot
- `safari_annotated_{timestamp}.png` - With bounding box and crosshair

### test_vision_coordinates.py
Comprehensive coordinate detection test with multiple targets.

```bash
python scripts/test_components/test_vision_coordinates.py
```

**Tests:**
1. Find Safari icon
2. Find Safari URL bar
3. Find all dock icons
4. General scene understanding

**Output:**
- `original_{timestamp}.png` - Original screenshot
- `annotated_{timestamp}.png` - With all detected elements
- `comparison_{timestamp}.png` - Side-by-side view

## How It Works

### 1. Vision Model Detection
The vision model (LLaVA) analyzes the screenshot and returns bounding boxes:

```
Prompt: "Find the Safari icon. Return coordinates as <box>(x1,y1,x2,y2)</box>"
Response: "<box>(123,456,234,567)</box>"
```

Coordinates are normalized 0-1000, then converted to pixels.

### 2. Coordinate Parsing
```python
def parse_bbox(text, img_width, img_height):
    # Extract: <box>(123,456,234,567)</box>
    pattern = r'<box>\((\d+),(\d+),(\d+),(\d+)\)</box>'
    x1, y1, x2, y2 = normalized coords (0-1000)

    # Convert to pixels
    px1 = (x1 / 1000.0) * img_width
    # ... etc
```

### 3. Visualization
```python
def draw_bbox(image, bbox, label, color):
    # Draw rectangle
    draw.rectangle([x1, y1, x2, y2], outline=color, width=4)

    # Draw crosshair at center (click point)
    center_x = (x1 + x2) // 2
    center_y = (y1 + y2) // 2
    draw.line(...crosshair...)

    # Draw label
    draw.text((x1, y1), label, fill="white")
```

### 4. Click Action
```python
# Calculate click point (center of bounding box)
center_x = (x1 + x2) // 2
center_y = (y1 + y2) // 2

# Execute click
click_action = ClickAction(x=center_x, y=center_y)
await click_action.execute()
```

## Vision Model Setup

The test uses **LLaVA** vision model by default.

### Download Model
```bash
ollama pull llava
```

Size: ~4.1 GB
Time: ~4-5 minutes

### Alternative Models

#### Llama 3.2 Vision (11B)
```bash
ollama pull llama3.2-vision
```
Better accuracy, larger model (~7GB)

#### Qwen 2.5 VL
```bash
ollama pull qwen2.5vl
```
Specialized for bounding boxes (~8GB)

### Configure Model
Edit `.env` or set environment variable:
```bash
export AGENT_VISION_MODEL=llava
# or
export AGENT_VISION_MODEL=llama3.2-vision
# or
export AGENT_VISION_MODEL=qwen2.5vl
```

## Expected Output

### Dry Run Mode
```
==================================================================
TEST: Find and Click Safari
==================================================================

[1] Checking vision model...
  ✅ Model available

[2] Capturing current screen...
  Screen: 3024x1964
  Saved: safari_original_20260202_091234.png

[3] Finding Safari icon with vision model...
  Asking model to locate Safari...

  Model response:
  '<box>(145,892,198,945)</box>'

  ✅ Safari icon found!
    Normalized coords: (145, 892, 198, 945)
    Pixel coords: (438, 1752, 598, 1856)
    Size: 160x104 pixels
    Click point (center): (518, 1804)

  Saved annotated: safari_annotated_20260202_091234.png

[4] Clicking Safari icon...
  ⏭️  DRY RUN - Would click at (518, 1804)
  To actually click, run with: --execute

==================================================================
SUCCESS
==================================================================
  Vision model: ✅ Found Safari
  Coordinates: ✅ Parsed correctly
  Visualization: ✅ safari_annotated_20260202_091234.png
==================================================================
```

### Execute Mode
Same as above, plus:
```
[4] Clicking Safari icon...
  🖱️  Clicking at (518, 1804)...
  ✅ Click executed successfully!
  Safari should now be opening...

  Click: ✅ Executed
```

## Troubleshooting

### Model Not Found
```
❌ Model llava not available
Run: ollama pull llava
```

**Solution:** Download the model (see above)

### No Bounding Box in Response
```
⚠️ Could not find bounding box in response
The model may not support bbox format or Safari not visible
```

**Possible causes:**
1. Safari icon not visible on screen
2. Model doesn't support `<box>` format
3. Try different prompt or model

**Solutions:**
- Make sure Safari icon is visible in Dock
- Try llama3.2-vision or qwen2.5vl
- Check the model's actual response

### Click Coordinates Out of Bounds
```
❌ Click coordinates out of bounds!
```

**Solution:**
- Verify screen size matches
- Check coordinate conversion math
- Ensure Safari is on main display

## Next Steps

### 1. Test Safari URL Bar
Once Safari opens, test finding and clicking the URL bar:

```python
prompt = "Find the Safari URL address bar where you type web addresses"
# Parse coordinates
# Click on URL bar
```

### 2. Test Text Input
Type a URL into the address bar:

```python
type_action = TypeAction(text="https://google.com")
await type_action.execute()
```

### 3. Test Navigation
Press Enter to navigate:

```python
hotkey_action = HotkeyAction("return")
await hotkey_action.execute()
```

### 4. Full Workflow
Combine all steps:
1. Find and click Safari
2. Wait for Safari to open
3. Find and click URL bar
4. Type URL
5. Press Enter

## Files

- `scripts/test_safari_click.py` - Main Safari clicking test
- `scripts/test_components/test_vision_coordinates.py` - Comprehensive coordinate test
- `scripts/test_components/output/` - Test screenshots and visualizations

## Requirements

- ✅ Ollama running
- ✅ Vision model downloaded (llava, llama3.2-vision, or qwen2.5vl)
- ✅ Accessibility permission (for clicking)
- ✅ Screen Recording permission (for screenshots)

---

**Note:** Always run in dry-run mode first to verify detection before actually clicking!
