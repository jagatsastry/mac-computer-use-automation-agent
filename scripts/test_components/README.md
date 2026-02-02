# Component Testing Scripts

Real component tests that use actual implementations (no mocks).

## Test Scripts

### 1. test_ollama_connection.py
Tests Ollama server connection and model availability.

```bash
python scripts/test_components/test_ollama_connection.py
```

Tests:
- Ollama server health
- Model listing
- Vision model availability (qwen2-vl)
- Text model availability (gemma2:9b)
- Basic text generation

### 2. test_text_model.py
Tests text model (Gemma 2) with real prompts.

```bash
python scripts/test_components/test_text_model.py
```

Tests:
- Model availability
- Simple completion
- Reasoning tasks
- JSON output
- Streaming generation

### 3. test_vision_model.py
Tests vision model (Qwen2-VL) with real screenshots.

```bash
python scripts/test_components/test_vision_model.py
```

Tests:
- Model availability
- Screenshot capture
- Image understanding
- UI element detection
- Bounding box detection
- Text recognition (OCR)

**Output:** Saves test images to `scripts/test_components/output/`

### 4. test_screen_capture.py
Tests screen capture functionality.

```bash
python scripts/test_components/test_screen_capture.py
```

Tests:
- Screen size detection
- Full screenshot capture
- Region capture
- Base64 encoding

**Output:** Saves screenshots to `scripts/test_components/output/`

**Note:** Requires Screen Recording permission in System Settings.

### 5. test_actions_real.py
Tests action system (PyAutoGUI) in safe mode.

```bash
# Safe mode (validation only)
python scripts/test_components/test_actions_real.py

# Execute real actions (CAUTION)
python scripts/test_components/test_actions_real.py --execute
```

Tests:
- Mouse position detection
- Click action validation
- Type action validation
- Hotkey action validation
- Wait action execution
- PyAutoGUI features

**Note:** Requires Accessibility permission. Use `--execute` flag carefully.

### 6. test_config_real.py
Tests configuration system with real files.

```bash
python scripts/test_components/test_config_real.py
```

Tests:
- Default configuration loading
- .env file loading
- Environment variable override
- JSON config file loading
- Configuration validation
- Current configuration display

## Running All Tests

```bash
# From project root
cd /Users/jagatp/workspace/macos-automation-agent

# Run all component tests
for script in scripts/test_components/test_*.py; do
    echo "Running $script..."
    python "$script"
    echo
done
```

Or use the convenience script:

```bash
./scripts/run_component_tests.sh
```

## Prerequisites

1. **Ollama running:**
   ```bash
   ollama list  # Check if running
   ```

2. **Models downloaded:**
   ```bash
   ollama pull gemma2:9b
   ollama pull qwen2-vl
   ```

3. **macOS Permissions:**
   - System Settings → Privacy & Security → Accessibility → Add Terminal
   - System Settings → Privacy & Security → Screen Recording → Add Terminal

4. **Virtual environment activated:**
   ```bash
   source venv/bin/activate
   ```

## Output

Test scripts create output files in:
- `scripts/test_components/output/` - Screenshots and test images

## Comparison with Unit Tests

| Aspect | Unit Tests | Component Tests |
|--------|-----------|-----------------|
| **Location** | `tests/` | `scripts/test_components/` |
| **Mocking** | Heavy mocking | No mocks, real implementations |
| **Speed** | Fast (~1.5s) | Slower (depends on models) |
| **Purpose** | Code correctness | Integration verification |
| **Dependencies** | Minimal | Requires Ollama, permissions |
| **CI/CD** | Yes | No (environment-specific) |

## Notes

- These tests use real AI models and may take longer to run
- Some tests require macOS permissions
- Vision model tests require qwen2-vl (~8GB download)
- All tests are safe and non-destructive
- Use `--execute` flag with caution on action tests
