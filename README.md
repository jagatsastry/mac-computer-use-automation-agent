# macOS Desktop Automation Agent

A fully local desktop automation agent for macOS that uses vision AI to understand and execute automation tasks.

## Features

- **Vision-based automation**: Uses Qwen2-VL via Ollama for screen understanding
- **Natural language prompts**: Describe automation tasks in plain English
- **Fully local**: All processing happens on your machine via Ollama
- **CLI interface**: Simple command-line tool

## Requirements

- macOS 11.0 or later
- Python 3.11 or later
- Ollama running locally

## Quick Start

```bash
# Install dependencies
pip install -e .

# Start Ollama (in another terminal)
ollama serve

# Pull required models (~14GB)
ollama pull qwen2-vl
ollama pull gemma2:9b

# Run automation
automation-agent "Click on Safari icon"
```

## Installation

```bash
# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install package
pip install -e ".[dev]"

# Copy environment template
cp .env.example .env
```

## Usage

```bash
# Basic usage
automation-agent "Open Calculator"

# Dry run mode
automation-agent --dry-run "Test prompt"

# Verbose logging
automation-agent --verbose "Complex task"

# Custom Ollama host
automation-agent --ollama-host http://192.168.1.100:11434 "Task"
```

### Molmo Vision Mode

Use Molmo for vision-based coordinate grounding:

```bash
# Install local Molmo runtime dependencies (optional but recommended)
pip install -e ".[molmo]"

# Uses Molmo backend when available
automation-agent --molmo "Book a table for 2 in San Jose tonight at 7pm"
```

Molmo backend resolution order:
- OpenRouter Molmo when `AGENT_OPENROUTER_API_KEY` (or `OPENROUTER_API_KEY`) is set
- Local Ollama `molmo` model if installed
- Local HuggingFace Molmo model (`allenai/MolmoE-1B-0924`) when transformers dependencies are installed
- Automatic fallback to `qwen3-vl` if Molmo is unavailable

### Restaurant-Focused Workflow

For a higher-success reservation flow, run restaurant mode:

```bash
automation-agent --restaurant-only "Find me dinner in San Jose"
```

This mode:
- Asks clarifying questions (cuisine, location, date/time, party size)
- Aggregates options from OpenTable, Yelp, and Google
- Asks you to pick a preferred option/provider
- Continues with reservation automation
- Pauses for manual login if a sign-in screen appears
- Persists preferences in `MEMORY.md` for future runs

### Hammerspoon Integration (Beta)

Use Hammerspoon for more robust action execution (clicking, typing):

1. Install Hammerspoon: `brew install --cask hammerspoon`
2. Ensure Hammerspoon is running and accessible in PATH (`hs` command).
3. Run with flag:

```bash
automation-agent --hammerspoon "Open Safari"
```

This generates Lua scripts and executes them via `hs` CLI, offering better reliability than default AppleScript/PyAutoGUI actions.

## macOS Permissions

The agent requires:
- **Accessibility**: For mouse/keyboard control
- **Screen Recording**: For taking screenshots

Grant these in: System Settings → Privacy & Security

## Development

```bash
# Run tests
pytest

# Format code
black src/ tests/

# Lint
ruff check src/ tests/

# Type check
mypy src/
```

## Molmo Evaluation Assets

To benchmark Molmo for restaurant automation:

- `MOLMO_EVALUATION_PLAN.md` - A/B protocol and success criteria
- `MOLMO_EVAL_TASKS.json` - Fixed 20-task benchmark checklist
- `MOLMO_EVAL_RESULTS_TEMPLATE.csv` - Results logging template

## Project Status

- ✅ Phase 1: Project setup & CLI (Complete)
- 🚧 Phase 2: LLM infrastructure (In Progress)
- ⏳ Phase 3: Perception layer
- ⏳ Phase 4: Action layer
- ⏳ Phase 5: Agent core
- ⏳ Phase 6: Integration & testing

## Author

Jagat Pudipeddi

## License

MIT
