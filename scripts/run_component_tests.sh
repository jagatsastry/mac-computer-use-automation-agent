#!/bin/bash
# Run all component tests

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=========================================="
echo "Running Component Tests"
echo "=========================================="
echo

# Activate venv if not already activated
if [ -z "$VIRTUAL_ENV" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
fi

# Test scripts directory
TEST_DIR="scripts/test_components"

# Array of test scripts in preferred order
tests=(
    "test_config_real.py"
    "test_ollama_connection.py"
    "test_text_model.py"
    "test_screen_capture.py"
    "test_actions_real.py"
    "test_vision_model.py"
)

# Track results
passed=0
failed=0
skipped=0

# Run each test
for test in "${tests[@]}"; do
    test_path="$TEST_DIR/$test"

    if [ ! -f "$test_path" ]; then
        echo "⚠️  Test not found: $test"
        ((skipped++))
        continue
    fi

    echo "▶️  Running: $test"
    echo "----------------------------------------"

    if python "$test_path"; then
        ((passed++))
        echo "✅ PASSED: $test"
    else
        ((failed++))
        echo "❌ FAILED: $test"
    fi

    echo
    echo
done

# Summary
echo "=========================================="
echo "Component Tests Summary"
echo "=========================================="
echo "✅ Passed:  $passed"
echo "❌ Failed:  $failed"
echo "⏭️  Skipped: $skipped"
echo "=========================================="

if [ $failed -eq 0 ]; then
    echo "🎉 All tests passed!"
    exit 0
else
    echo "⚠️  Some tests failed"
    exit 1
fi
