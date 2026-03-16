#!/bin/bash
# Test Molmo grounding using the EXACT same prompt as the agent's find_element.
# Usage: ./molmo_grounding_test.sh <screenshot_path> "<element description>"
#        ./molmo_grounding_test.sh <screenshot_path> --prompt-override "<full prompt>"
# Example: ./molmo_grounding_test.sh logs/runs/260314_221202/screenshots/221254_step_03_observe.png "Order for a pair of shoes"

set -euo pipefail

PROMPT_OVERRIDE=""
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --prompt-override)
            PROMPT_OVERRIDE="$2"
            shift 2
            ;;
        *)
            ARGS+=("$1")
            shift
            ;;
    esac
done

IMAGE="${ARGS[0]:?Usage: $0 <screenshot.png> \"<element description>\"}"
ELEMENT="${ARGS[1]:-}"
PORT="${ARGS[2]:-8091}"
MODEL="${ARGS[3]:-mlx-community/Molmo-7B-D-0924-3bit}"

if [ ! -f "$IMAGE" ]; then
    echo "File not found: $IMAGE" >&2
    exit 1
fi

if [[ -n "$PROMPT_OVERRIDE" ]]; then
    PROMPT="$PROMPT_OVERRIDE"
else
    if [[ -z "$ELEMENT" ]]; then
        echo "Usage: $0 <screenshot.png> \"<element description>\" OR $0 <screenshot.png> --prompt-override \"<full prompt>\"" >&2
        exit 1
    fi
    # Inline the exact prompt from src/automation_agent/vision/prompts/find_element.md
    PROMPT="Look at this screenshot of a macOS desktop. I need you to find the following UI element:

${ELEMENT}

If you can find the element, respond with exactly:
FOUND: x=<number>, y=<number>, confidence=<0.0-1.0>

where confidence indicates how certain you are (1.0 = absolutely sure, 0.5 = uncertain, 0.0 = guessing).

If you cannot find this element, respond with exactly:
NOT_FOUND

Only respond with one of these formats, nothing else."
fi

echo "Image: $IMAGE"
echo "Element: ${ELEMENT:-(prompt override)}"
echo "Server: http://localhost:$PORT"
echo "Model: $MODEL"
echo "---"

# Escape the prompt for JSON embedding
JSON_PROMPT=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$PROMPT")

curl -s "http://localhost:${PORT}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "'"$MODEL"'",
    "messages": [{"role": "user", "content": [
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,'"$(base64 -i "$IMAGE")"'"}},
      {"type": "text", "text": '"$JSON_PROMPT"'}
    ]}],
    "max_tokens": 256,
    "temperature": 0.0
  }' | python3 -c "
import sys, json
data = json.load(sys.stdin)
text = data['choices'][0]['message']['content']
print(f'Response: {text}')
"
