# Technology Stack

**Analysis Date:** 2026-03-20

## Languages

**Primary:**
- Python 3.9+ - All core code, LLM clients, vision coordination, automation logic

## Runtime

**Environment:**
- Python 3.9+ (targets 3.11 for type hints)

**Package Manager:**
- pip (setuptools/hatchling)
- Lockfile: Not detected (pin dependencies in pyproject.toml)

## Frameworks & Core Libraries

**LLM & Vision:**
- ollama 0.1.6+ - Local Ollama text model client (async)
- anthropic 0.40.0+ - Claude API client (optional, `pip install -e ".[anthropic]"`)
- google-genai 1.0.0+ - Google Gemini API client (optional, `pip install -e ".[gemini]"`)
- httpx 0.27.0+ - Async HTTP client for OpenAI-compatible APIs (llama.cpp, vLLM, etc.)

**Configuration & Env:**
- pydantic 2.6.0+ - Config schema and validation
- pydantic-settings 2.1.0+ - Environment variable loading (prefix: AGENT_)
- python-dotenv 1.0.0+ - .env file support

**Desktop Automation (macOS):**
- pyautogui 0.9.54+ - Mouse/keyboard control
- pyobjc-core 10.1+ - macOS Cocoa/ObjC bindings
- pyobjc-framework-Cocoa 10.1+ - macOS UI framework access
- pyobjc-framework-Quartz 10.1+ - Screen capture and graphics
- pync 2.0.3+ - macOS notifications

**Image & Vision:**
- pillow 10.2.0+ - Image processing
- opencv-python-headless 4.9.0+ - Computer vision (headless)
- pytesseract 0.3.10+ - OCR via Tesseract
- pynput 1.7.6+ - Cross-platform input simulation

**Logging & Observability:**
- structlog 24.1.0+ - Structured JSON logging with context
- colorama 0.4.6+ - Terminal color output

**Data & Math:**
- numpy 1.24.0+ - Numerical arrays
- pyyaml 6.0+ - YAML parsing (skill templates)

**Testing:**
- pytest 8.0.0+ - Test runner
- pytest-cov 4.1.0+ - Coverage reporting
- pytest-asyncio 0.23.0+ - Async test support
- pytest-mock 3.12.0+ - Mocking framework

**Code Quality:**
- black 24.1.0+ - Code formatter (line-length: 100)
- ruff 0.2.0+ - Linter (E, W, F, I, B, C4, UP rules)
- mypy 1.8.0+ - Static type checker (Python 3.11 target)

## Optional Dependencies

**Embeddings (Skill Matching):**
- fastembed 0.3+ - Fast embedding model for skill retrieval (BAAI/bge-small-en-v1.5)

**Molmo Vision Local:**
- transformers 4.46.x - HuggingFace transformers
- torch 2.8.0+ - PyTorch
- torchvision 0.23.0+ - Vision utilities
- einops 0.8.0+ - Tensor operations
- accelerate 1.10.0+ - Distributed training utilities

## Configuration

**Environment Variables (AGENT_ prefix):**
- `AGENT_MODEL_PROVIDER` - Provider: local, anthropic, gemini, openai (default: local)
- `AGENT_VISION_SERVER_URL` - Vision server endpoint (default: http://localhost:8080)
- `AGENT_VISION_MODEL` - Vision model name (default: qwen3-vl)
- `AGENT_TEXT_SERVER_URL` - Text/planning server (default: http://localhost:11434 for Ollama)
- `AGENT_TEXT_MODEL` - Text model name (default: gemma2:9b)
- `AGENT_VISION_SERVER_TIMEOUT` - Timeout in seconds (default: 300)

**API Keys:**
- `ANTHROPIC_API_KEY` - Claude API authentication
- `GEMINI_API_KEY` / `GOOGLE_API_KEY` - Google Gemini API authentication
- `OPENAI_API_KEY` - OpenAI GPT API authentication
- `OPENROUTER_API_KEY` - OpenRouter API for Molmo vision (optional)

**Per-Step Model Routing:**
- `AGENT_PLANNING_MODEL` - Override planning model (e.g., gemini:gemini-2.5-flash)
- `AGENT_GROUNDING_MODEL_PROVIDER` - Override grounding model (e.g., openai:gpt-5.4)
- `AGENT_VERIFICATION_MODEL` - Override verification model
- `AGENT_SCREEN_DESCRIPTION_MODEL` - Override screen description model (e.g., local:molmo)
- `AGENT_REFLECTION_MODEL` - Override async reflection model

**Automation & Safety:**
- `AGENT_ACTION_DELAY` - Delay between actions in seconds (default: 0.5)
- `AGENT_MAX_RETRIES` - Maximum retry attempts (default: 3)
- `AGENT_MAX_ITERATIONS` - Max steps before abort (default: 20)
- `AGENT_CONFIRM_DESTRUCTIVE` - Mode: always, smart, never (default: smart)
- `AGENT_DRY_RUN` - Log without executing destructive actions (default: false)

**Feature Flags:**
- `AGENT_SOM_ENABLED` - Set-of-Mark screenshot labels (default: false)
- `AGENT_DUAL_RESOLUTION_GROUNDING` - Full + crop to VLM (default: false)
- `AGENT_SKILL_EMBEDDING_ENABLED` - Embedding-based skill retrieval (default: false)
- `AGENT_LOOKAHEAD_ENABLED` - Pre-action outcome prediction (default: false)
- `AGENT_JS_VERIFICATION_ENABLED` - Browser JS injection for state verification (default: true)

**Logging & Persistence:**
- `AGENT_LOG_LEVEL` - Console log level (default: INFO)
- `AGENT_LOG_DIR` - Log directory (default: logs)
- `AGENT_EVENT_LOG_DIR` - Structured event logs (default: logs/runs)
- `AGENT_STATUS_UI` - Live UI mode: off, overlay (default: off)

**Skill Learning:**
- `AGENT_SKILL_LEARNING_ENABLED` - Post-run observation extraction (default: true)
- `AGENT_SKILL_LIBRARIAN_ENABLED` - Auto-promote observations to skills (default: true)

**Build:**
- setup: hatchling
- packages: `src/automation_agent`
- entry point: `automation-agent = automation_agent.__main__:main`

## Platform Requirements

**Development:**
- macOS 10.14+ (for pyobjc, osascript)
- Python 3.9+ with pip
- Xcode Command Line Tools (for osascript)

**Production / Testing:**
- macOS 10.14+ (Cocoa/Quartz APIs)
- Vision server running (Ollama, llama.cpp, or cloud API keys)
- Text server running (Ollama) or cloud API key (Anthropic/Gemini/OpenAI)

**Optional:**
- Tesseract OCR binary (for pytesseract)
- MLX framework (for local Molmo inference via custom scripts)

---

*Stack analysis: 2026-03-20*
