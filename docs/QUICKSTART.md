# Quick Start

```bash
cd /Users/jagatp/workspace/macos-automation-agent
```

## Prerequisites

1. **Python 3.9+** with the project installed:
   ```bash
   pip install -e ".[dev]"
   ```

2. **Hammerspoon** running with the bridge HTTP server on `localhost:27741`

3. **Anthropic API key** in your environment:
   ```bash
   export ANTHROPIC_API_KEY=sk-ant-...
   ```

4. **llama.cpp** (for local vision):
   ```bash
   brew install llama.cpp
   ```

5. **Vision model** (Qwen2.5-VL 7B, ~4.8GB download):
   ```bash
   python3 -c "
   from huggingface_hub import hf_hub_download
   hf_hub_download('Mungert/Qwen2.5-VL-7B-Instruct-GGUF', 'Qwen2.5-VL-7B-Instruct-q4_k_m.gguf', local_dir='$HOME/models/qwen2.5-vl-7b')
   hf_hub_download('Mungert/Qwen2.5-VL-7B-Instruct-GGUF', 'Qwen2.5-VL-7B-Instruct-mmproj-f16.gguf', local_dir='$HOME/models/qwen2.5-vl-7b')
   "
   ```

## Start the Vision Server

```bash
llama-server \
  -m ~/models/qwen2.5-vl-7b/Qwen2.5-VL-7B-Instruct-q4_k_m.gguf \
  --mmproj ~/models/qwen2.5-vl-7b/Qwen2.5-VL-7B-Instruct-mmproj-f16.gguf \
  --port 8090 -ngl 99
```

Verify it's running:
```bash
curl -s http://localhost:8090/v1/models
```

## Run the Agent

### With llama.cpp vision (local, recommended)

```bash
AGENT_VISION_SERVER_URL=http://localhost:8090 \
AGENT_VISION_MODEL=Qwen2.5-VL-7B-Instruct-q4_k_m.gguf \
AGENT_MODEL_PROVIDER=local \
python3 -m automation_agent "Show me the cheapest shirts for men on Amazon"
```

### With Claude API vision

```bash
AGENT_MODEL_PROVIDER=anthropic \
python3 -m automation_agent "Open Calculator and compute 7 * 8"
```

### Dry run (plan only, no execution)

```bash
python3 -m automation_agent --dry-run "Open Safari and search Google for weather"
```

### With debug logging

```bash
AGENT_LOG_LEVEL=DEBUG \
AGENT_VISION_SERVER_URL=http://localhost:8090 \
AGENT_VISION_MODEL=Qwen2.5-VL-7B-Instruct-q4_k_m.gguf \
AGENT_MODEL_PROVIDER=local \
python3 -m automation_agent "Your prompt here"
```

## Logs

After each run, detailed logs are written to:

```
/Users/jagatp/workspace/macos-automation-agent/logs/
├── automation_agent.log                    # Rolling log file (all runs)
└── runs/
    └── {run_id}/
        ├── trace.md                        # Human-readable step-by-step trace
        ├── events.jsonl                    # Machine-readable structured events
        └── screenshots/                    # Screenshots captured at each step
```

**View the latest run's trace:**
```bash
# Find the most recent run
ls -t logs/runs/ | head -1

# Read the trace (replace {run_id} with actual ID)
cat logs/runs/{run_id}/trace.md
```

**One-liner to view the latest trace:**
```bash
cat logs/runs/$(ls -t logs/runs/ | head -1)/trace.md
```

**Tail the live log:**
```bash
tail -f /Users/jagatp/workspace/macos-automation-agent/logs/automation_agent.log
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_MODEL_PROVIDER` | `local` | `local` (llama.cpp/Ollama) or `anthropic` (Claude API) |
| `AGENT_VISION_SERVER_URL` | `http://localhost:8080` | Vision server endpoint (OpenAI-compatible) |
| `AGENT_VISION_MODEL` | `qwen3-vl` | Vision model name or GGUF filename |
| `ANTHROPIC_API_KEY` | — | Required for planning (always uses Claude API) |
| `AGENT_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `AGENT_LOG_DIR` | `logs/` | Directory for log files |
| `AGENT_EVENT_LOG_DIR` | `logs/runs/` | Directory for per-run event logs |

## Example Prompts

```bash
# Calculator
python3 -m automation_agent "Open Calculator and compute 3 * 18"

# Amazon shopping (uses amazon-search skill)
python3 -m automation_agent "Show me the cheapest shirts for men on Amazon"
python3 -m automation_agent "Find the cheapest wireless mouse on Amazon"
python3 -m automation_agent "Search Amazon for USB-C cables sorted by price"

# Safari navigation
python3 -m automation_agent "Open Safari and go to news.ycombinator.com"
python3 -m automation_agent "Open https://weather.com in Safari"

# App management
python3 -m automation_agent "Open Notes and create a new note"
python3 -m automation_agent "Quit Safari"
python3 -m automation_agent "Open Finder and go to Downloads"

# Multi-step
python3 -m automation_agent "Open Calculator, type 42 * 17, and press equals"
python3 -m automation_agent "Open Safari, go to Google, and search for best coffee shops nearby"

# Dry run (see the plan without executing)
python3 -m automation_agent --dry-run "Order a pizza on DoorDash"
```

## Benchmark Vision Backends

Compare llama.cpp vs Ollama speed:

```bash
# Ensure both servers are running, then:
python3 scripts/benchmark_vision.py
```

## Tests

```bash
pytest                          # All tests
pytest tests/unit/              # Unit tests only (fast, all mocked)
pytest -m "not e2e"             # Skip e2e tests
pytest -k "test_plan_basic"     # Single test by name
```
