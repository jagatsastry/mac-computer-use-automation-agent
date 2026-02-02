#!/bin/bash
# Check status of automation agent setup

echo "======================================================================"
echo "macOS Desktop Automation Agent - Status Check"
echo "======================================================================"
echo ""

# Check Python
echo "Python Environment:"
if command -v python3 &> /dev/null; then
    echo "  ✓ Python installed: $(python3 --version)"
else
    echo "  ✗ Python not found"
fi
echo ""

# Check venv
echo "Virtual Environment:"
if [ -d "venv" ]; then
    echo "  ✓ Virtual environment exists"
    if [ -f "venv/bin/automation-agent" ]; then
        echo "  ✓ automation-agent CLI installed"
    else
        echo "  ✗ automation-agent not installed (run: pip install -e .)"
    fi
else
    echo "  ✗ Virtual environment not found (run: python3 -m venv venv)"
fi
echo ""

# Check Ollama
echo "Ollama Service:"
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "  ✓ Ollama server running"

    # Check models
    if curl -s http://localhost:11434/api/tags | grep -q "qwen2-vl"; then
        echo "  ✓ qwen2-vl model available"
    else
        echo "  ⚠ qwen2-vl not found (run: ollama pull qwen2-vl)"
    fi

    if curl -s http://localhost:11434/api/tags | grep -q "gemma2"; then
        echo "  ✓ gemma2 model available"
    else
        echo "  ⚠ gemma2 not found (run: ollama pull gemma2:9b)"
    fi
else
    echo "  ✗ Ollama not running (start with: ollama serve)"
fi
echo ""

# Check logs
echo "Logs:"
if [ -f "logs/automation_agent.log" ]; then
    log_size=$(du -h logs/automation_agent.log | cut -f1)
    echo "  ✓ Log file exists (${log_size})"
else
    echo "  - No logs yet (will be created on first run)"
fi
echo ""

# Check tests
echo "Tests:"
if [ -f "tests/test_integration.py" ]; then
    echo "  ✓ Integration tests available"
    echo "    Run with: pytest tests/test_integration.py -v -s"
else
    echo "  ✗ Integration tests not found"
fi
echo ""

echo "======================================================================"
echo "Quick Commands:"
echo "======================================================================"
echo "  Test CLI:        automation-agent --version"
echo "  Dry run:         automation-agent --dry-run 'Test prompt'"
echo "  Run tests:       pytest tests/test_integration.py -v -s"
echo "  View logs:       tail -f logs/automation_agent.log"
echo "  Pull models:     ollama pull qwen2-vl && ollama pull gemma2:9b"
echo "======================================================================"
