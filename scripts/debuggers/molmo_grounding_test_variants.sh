#!/bin/bash
# Test multiple prompt variants to see which ones cause Molmo to hallucinate vs correctly say not found.
# Usage: ./molmo_grounding_test_variants.sh <screenshot_path> "<element description>"

set -euo pipefail

IMAGE="${1:?Usage: $0 <screenshot.png> \"<element description>\"}"
ELEMENT="${2:?Usage: $0 <screenshot.png> \"<element description>\"}"
PORT="${3:-8091}"
MODEL="${4:-mlx-community/Molmo-7B-D-0924-3bit}"

if [ ! -f "$IMAGE" ]; then
    echo "File not found: $IMAGE" >&2
    exit 1
fi

B64=$(base64 -i "$IMAGE")

call_molmo() {
    local LABEL="$1"
    local PROMPT="$2"
    local JSON_PROMPT
    JSON_PROMPT=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$PROMPT")

    echo ""
    echo "=== $LABEL ==="
    curl -s "http://localhost:${PORT}/v1/chat/completions" \
      -H "Content-Type: application/json" \
      -d '{
        "model": "'"$MODEL"'",
        "messages": [{"role": "user", "content": [
          {"type": "image_url", "image_url": {"url": "data:image/png;base64,'"$B64"'"}},
          {"type": "text", "text": '"$JSON_PROMPT"'}
        ]}],
        "max_tokens": 256,
        "temperature": 0.0
      }' | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data['choices'][0]['message']['content'].strip())
"
}

echo "Image: $IMAGE"
echo "Element: $ELEMENT"
echo "---"

# Variant 1: Simple "Point to"
call_molmo "V1: Point to" \
    "Point to ${ELEMENT}"

# Variant 2: Current find_element.md (FOUND/NOT_FOUND format)
call_molmo "V2: Current prompt (FOUND/NOT_FOUND)" \
    "Look at this screenshot of a macOS desktop. I need you to find the following UI element:

${ELEMENT}

If you can find the element, respond with exactly:
FOUND: x=<number>, y=<number>, confidence=<0.0-1.0>

where confidence indicates how certain you are (1.0 = absolutely sure, 0.5 = uncertain, 0.0 = guessing).

If you cannot find this element, respond with exactly:
NOT_FOUND

Only respond with one of these formats, nothing else."

# Variant 3: Ask if visible first, then coordinates
call_molmo "V3: Existence check first" \
    "Look at this screenshot of a macOS desktop. Is the following UI element visible?

${ELEMENT}

First answer YES or NO. If YES, then provide coordinates as: x=<number>, y=<number>"

# Variant 4: NOT_FOUND first in the format
call_molmo "V4: NOT_FOUND listed first" \
    "Look at this screenshot. Find this element: ${ELEMENT}

If you CANNOT find it, respond: NOT_FOUND
If you CAN find it, respond: FOUND: x=<number>, y=<number>, confidence=<0.0-1.0>"

# Variant 5: Natural language with explicit "say so"
call_molmo "V5: Natural language" \
    "Look at this screenshot. Can you see: ${ELEMENT}

If it's not visible in the screenshot, say \"I cannot find this element\". If it is visible, point to it."
