"""SoM screenshot annotator — draws numbered labels on screenshots."""

import base64
import io
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageDraw


def annotate_screenshot(
    screenshot_b64: str,
    elements: List[Dict[str, Any]],
    screen_size: Tuple[int, int],
    max_labels: int = 20,
) -> str:
    """AC-11: Draw numbered bounding boxes on screenshot at element positions.

    Args:
        screenshot_b64: Base64-encoded JPEG screenshot.
        elements: List of AX element dicts. Accepts both JXA format
            (center_x, center_y, width, height) and pyobjc format
            (position, size). Max ``max_labels`` elements drawn (AC-12).
        screen_size: (screen_width, screen_height) in logical pixels,
            used for coordinate mapping from screen-space to image-space.
        max_labels: Maximum number of labels to draw (default 20, AC-12).

    Returns:
        Base64-encoded JPEG with numbered bounding box overlays.
    """
    img_bytes = base64.b64decode(screenshot_b64)
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    img_w, img_h = img.size
    screen_w, screen_h = screen_size

    # Scale factors: screen coords -> image coords
    sx = img_w / screen_w
    sy = img_h / screen_h

    for idx, el in enumerate(elements[:max_labels]):
        cx, cy, w, h = _extract_element_bounds(el)

        # Map to image space
        ix = int(cx * sx)
        iy = int(cy * sy)
        iw = int(w * sx)
        ih = int(h * sy)

        # Draw bounding box
        left = max(0, ix - iw // 2)
        top = max(0, iy - ih // 2)
        right = min(img_w - 1, ix + iw // 2)
        bottom = min(img_h - 1, iy + ih // 2)

        # Skip degenerate boxes
        if right <= left or bottom <= top:
            continue

        color = _LABEL_COLORS[idx % len(_LABEL_COLORS)]
        draw.rectangle([left, top, right, bottom], outline=color, width=2)

        # Draw number label with anti-spoofing marker (security finding 9).
        label = f"\u25c6{idx + 1}"
        label_x = max(0, left - 2)
        label_y = max(0, top - 18)
        circle_r = 10
        draw.ellipse(
            [label_x, label_y, label_x + circle_r * 2, label_y + circle_r * 2],
            fill=color,
        )
        draw.text((label_x + 3, label_y + 2), label, fill=(255, 255, 255))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _extract_element_bounds(
    el: Dict[str, Any],
) -> Tuple[float, float, float, float]:
    """Normalize JXA and pyobjc element formats to (cx, cy, w, h) in screen pixels."""
    # JXA format
    if "center_x" in el:
        return (
            float(el["center_x"]),
            float(el["center_y"]),
            float(el.get("width", 40)),
            float(el.get("height", 20)),
        )
    # pyobjc format
    if "position" in el:
        pos = el["position"]
        size = el.get("size", (40, 20))
        x, y = float(pos[0]), float(pos[1])
        w, h = float(size[0]), float(size[1])
        return (x + w / 2, y + h / 2, w, h)
    # Fallback
    return (float(el.get("x", 0)), float(el.get("y", 0)), 40, 20)


_LABEL_COLORS = [
    (255, 0, 0), (0, 200, 0), (0, 0, 255), (255, 165, 0),
    (128, 0, 128), (0, 200, 200), (255, 0, 255), (200, 200, 0),
    (0, 128, 0), (128, 128, 255),
] * 2  # 20 colors for 20 max labels
