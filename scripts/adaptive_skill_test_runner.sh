#!/bin/bash
# Adaptive Skill Customer Test Runner
# Runs 6 blocks x 5 runs each = 30 runs total
# Each block is fully isolated (skill_learning + skill library reset)

set -uo pipefail
# Note: -e removed intentionally — agent runs may fail and we want to continue

PROJ_DIR="/Users/jagatp/workspace/macos-automation-agent"
SKILL_LIB="$PROJ_DIR/src/automation_agent/skills/library"
LEARNING_DIR="$PROJ_DIR/logs/skill_learning"
RUNS_DIR="$PROJ_DIR/logs/runs"
BACKUP_DIR="/tmp/adaptive_test_backup"
CSV_FILE="/tmp/adaptive_test_runs.csv"
PYTHON="$PROJ_DIR/.venv/bin/python"

# ---- Setup ----
mkdir -p "$BACKUP_DIR"
rm -f "$CSV_FILE"

# Snapshot clean skill library
rm -rf "$BACKUP_DIR/skill_library_clean"
cp -r "$SKILL_LIB" "$BACKUP_DIR/skill_library_clean"

# Snapshot skill hashes
find "$SKILL_LIB" -name "*.md" | sort | xargs md5 > /tmp/skill_md5_before.txt

reset_state() {
    echo "  [RESET] Wiping skill_learning + restoring skill library..."
    rm -rf "$LEARNING_DIR"
    mkdir -p "$LEARNING_DIR/promotions"
    rm -rf "$SKILL_LIB"
    cp -r "$BACKUP_DIR/skill_library_clean" "$SKILL_LIB"
}

run_prompt() {
    local PROMPT_ID="$1"
    local ROUND="$2"
    local PROMPT="$3"

    echo "  [$PROMPT_ID] Round $ROUND: $PROMPT"
    $PYTHON -m automation_agent --status-ui overlay --verbose-overlay "$PROMPT" 2>&1 || true

    # Capture run ID (avoid SIGPIPE from head closing ls pipe)
    LATEST_RUN=$(ls -t "$RUNS_DIR/" 2>/dev/null | head -1 || true)
    if [ -n "$LATEST_RUN" ]; then
        echo "$PROMPT_ID,$ROUND,$LATEST_RUN" >> "$CSV_FILE"
        echo "  [$PROMPT_ID] Round $ROUND -> run=$LATEST_RUN"
    else
        echo "  [$PROMPT_ID] Round $ROUND -> NO RUN DIRECTORY FOUND"
    fi
}

run_block() {
    local BLOCK_NAME="$1"
    local PROMPT_ID="$2"
    local PROMPT="$3"
    local RUNS="${4:-5}"

    echo ""
    echo "========================================"
    echo "Block: $BLOCK_NAME ($PROMPT_ID x $RUNS)"
    echo "========================================"
    reset_state

    for i in $(seq 1 "$RUNS"); do
        run_prompt "$PROMPT_ID" "$i" "$PROMPT"
        echo ""
        # Quick observation count between runs
        shopt -s nullglob
        for f in "$LEARNING_DIR"/*.jsonl; do
            echo "    obs: $(basename "$f" .jsonl) = $(wc -l < "$f" | tr -d ' ')"
        done
        shopt -u nullglob
        # Check for promotions
        HISTORY="$LEARNING_DIR/promotions/history.jsonl"
        [ -f "$HISTORY" ] && echo "    promotions: $(wc -l < "$HISTORY" | tr -d ' ')" || echo "    promotions: 0"
    done

    # Post-block snapshot
    echo "  [SNAPSHOT] Post-block skill diffs:"
    diff /tmp/skill_md5_before.txt <(find "$SKILL_LIB" -name "*.md" | sort | xargs md5) 2>&1 || true
}

echo "============================================"
echo "Adaptive Skill Customer Test"
echo "Started: $(date)"
echo "Config: read from AgentConfig (see .env)"
echo "============================================"

# Block 1: A1 x5 — amazon-search baseline
run_block "A1-amazon-search-baseline" "A1" \
    "Search Amazon for wireless earbuds under \$25"

# Block 2: C1 x5 — amazon-search replan + promotion
run_block "C1-amazon-search-replan" "C1" \
    "Search Amazon for USB-C hub sorted by customer reviews"

# Block 3: A2 x5 — buy-on-target baseline
run_block "A2-buy-on-target-baseline" "A2" \
    "Add a coffee maker to my cart on Target"

# Block 4: C2 x5 — buy-on-target replan + promotion
run_block "C2-buy-on-target-replan" "C2" \
    "Add a phone case to my cart on Target and select gift wrapping"

# Block 5: B1 x5 — walmart return observation accumulation
run_block "B1-walmart-return" "B1" \
    "Return a pair of shoes I bought on Walmart"

# Block 6: B2 x5 — bestbuy no-match documentation
run_block "B2-bestbuy-no-match" "B2" \
    "Find the cheapest laptop on Best Buy"

echo ""
echo "============================================"
echo "All blocks complete: $(date)"
echo "Runs CSV: $CSV_FILE"
echo "============================================"
