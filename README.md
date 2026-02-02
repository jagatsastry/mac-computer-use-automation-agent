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
