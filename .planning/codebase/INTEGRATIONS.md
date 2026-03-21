# External Integrations

**Analysis Date:** 2026-03-20

## LLM Providers

**Anthropic Claude API:**
- Service: Claude for text generation and vision
- SDK: anthropic 0.40.0+
- Auth: `ANTHROPIC_API_KEY` environment variable
- Models: claude-sonnet-4-20250514 (default, configurable)
- Usage: `src/automation_agent/planner/planner.py` (_call_anthropic_llm), `src/automation_agent/vision/coordinator.py` (_call_anthropic_vision)
- Cost: Per-token billing via Anthropic

**Google Gemini API:**
- Service: Gemini for text generation and multimodal vision
- SDK: google-genai 1.0.0+
- Auth: `GEMINI_API_KEY` or `GOOGLE_API_KEY` environment variable
- Models: gemini-2.5-flash (default, configurable)
- Usage: `src/automation_agent/planner/planner.py` (_call_gemini_llm), `src/automation_agent/vision/coordinator.py` (_call_gemini_vision)
- Cost: Per-token billing via Google Cloud

**OpenAI GPT API:**
- Service: GPT text generation and vision (Responses API for pixel-level grounding)
- SDK: Custom httpx-based implementation (no openai SDK required)
- Auth: `OPENAI_API_KEY` environment variable
- Models: gpt-5.4, gpt-4.1, gpt-4o (configurable)
- Usage: `src/automation_agent/llm/openai_client.py` (OpenAIClient), `src/automation_agent/planner/planner.py` (_call_openai_llm), `src/automation_agent/vision/coordinator.py` (_call_openai_vision)
- Endpoints: https://api.openai.com/v1/responses (computer tool for grounding), /v1/chat/completions (standard)
- Cost: Per-token billing via OpenAI

**Ollama (Local Text):**
- Service: Local LLM inference via Ollama server
- Client: ollama 0.1.6+ (async)
- Connection: `AGENT_TEXT_SERVER_URL` (default: http://localhost:11434)
- Models: gemma2:9b (default, any Ollama-hosted model)
- Usage: `src/automation_agent/planner/planner.py` (_call_ollama_native, _call_openai_compat)
- Features: Native /api/chat with GBNF grammar constraints for structured JSON output
- Cost: Free (self-hosted)

**Local Vision Servers (OpenAI-Compatible):**
- Services: llama.cpp, vLLM, MLX servers (any OpenAI-compatible /v1/chat/completions endpoint)
- Connection: `AGENT_VISION_SERVER_URL` (default: http://localhost:8080)
- Models: qwen3-vl, qwen2.5-vl, qwen2-vl, molmo, molmo2 (configurable)
- Usage: `src/automation_agent/vision/coordinator.py` (_call_local_vision, _call_grounding_model)
- Protocol: OpenAI-compatible /v1/chat/completions with image_url content
- Cost: Free (self-hosted)

## Vision Model Backends

**Coordinate Space Registry:**
Located in `src/automation_agent/vision/coordinator.py` COORDINATE_SPACES dict:
- molmo: normalized_0_100 (Molmo v1, divide by 100)
- molmo2, qwen3-vl, qwen2.5-vl, qwen2-vl: normalized_0_1000 (divide by 1000)
- claude-sonnet-4-20250514, gpt-*: pixel coordinates (divide by image width/height)

**Supported Models & Detection:**
- Explicit registration per model — raises ValueError for unknown models
- Case-insensitive prefix matching for GGUF filenames and HuggingFace org paths
- Per-step routing: `config.resolve_step_model(step)` returns (provider, model) tuple

## OpenRouter (Optional Molmo Vision):**
- Service: Molmo vision via OpenRouter API
- SDK: Custom HTTP client (urllib.request)
- Auth: `OPENROUTER_API_KEY` environment variable
- Base URL: `https://openrouter.ai/api/v1` (configurable via AGENT_OPENROUTER_BASE_URL)
- Model: allenai/molmo-2-8b:free (default, configurable via AGENT_MOLMO_MODEL)
- Usage: `src/automation_agent/llm/molmo_client.py` (MolmoVisionClient)
- Cost: Per-token billing via OpenRouter

## Data Storage

**Skill Library:**
- Storage: File-based Markdown templates
- Location: `~/.claude/skills/<name>/SKILL.md` (user directory) or bundled defaults
- Config: `AGENT_SKILL_LIBRARY_PATH` to override
- Format: YAML frontmatter + Markdown steps with {{param}} placeholders

**Event Logs:**
- Format: JSONL (newline-delimited JSON)
- Location: `AGENT_EVENT_LOG_DIR` (default: logs/runs)
- Structure: Per-run subdirectories with timestamped entries
- Retention: Configurable directory cleanup (not automated)

**Skill Learning Observations:**
- Format: JSONL sidecars
- Location: `logs/skill_learning/{skill_name}.jsonl`
- Content: Alternative paths, checkpoints, user gates, anti-patterns from execution
- Promotion History: `logs/skill_learning/promotions/history.jsonl`

**Logs:**
- Format: Structured JSON (structlog) + rotating file handlers
- Location: `AGENT_LOG_DIR` (default: logs)
- Rotation: Max 10 MB per file, 5 backups (configurable)
- Levels: DEBUG (file), INFO (console), configurable

## File System

**Screenshot Persistence:**
- Storage: Local filesystem
- Saving: Per-action screenshots (step, observe, post-verification)
- Config: `AGENT_SAVE_STEP_SCREENSHOTS` (default: true)
- Format: JPEG base64 in event logs or PNG files in temp directories
- Cleanup: Manual (not automated)

## Authentication & Identity

**Browser State Verification (JS Injection):**
- Mechanism: JavaScript evaluation via AppleScript
- Browsers: Safari (with "Allow JavaScript from Apple Events" enabled), Chrome, Firefox, Arc
- Auth Required: Safari only (Develop > Developer Settings)
- Data Extracted: focused_value, selected_text, page_title, page_heading
- Implementation: `src/automation_agent/actuator/applescript_actuator.py` (_get_browser_js_batch)
- Fallback: Auto-disables per-browser on first failure; logs warning once
- Config: `AGENT_JS_VERIFICATION_ENABLED` (default: true)

**Accessibility API (macOS):**
- Mechanism: PyObjC bridge to Cocoa accessibility APIs
- Implementation: `src/automation_agent/perception/accessibility.py` (AccessibilityBridge)
- Data: UI element metadata (role, label, value, position)
- Usage: Fast lookup before vision grounding
- Auto-disabled: If initialization fails, falls back to vision-only
- Config: `AGENT_USE_ACCESSIBILITY` (default: true)

## Monitoring & Observability

**Structured Logging:**
- Framework: structlog
- Output: JSON to file + colored text to console
- Context: Per-run IDs, step numbers, durations, token counts

**No External Services:**
- Error tracking: Not configured (local only)
- APM: Not configured (local only)
- Metrics: Local event logs only

## CI/CD & Deployment

**Hosting:**
- Not applicable (macOS desktop agent, runs locally)

**Package Distribution:**
- PyPI: automation-agent (pip install automation-agent)
- Extras: dev, anthropic, gemini, embeddings, molmo, all
- Build: hatchling

**No External CI:**
- Tests run locally via pytest
- No automatic deployment pipeline

## Webhooks & Callbacks

**None Configured:**
- No outbound webhooks
- No incoming API callbacks
- No event subscriptions

## Environment Configuration

**Required for Cloud Providers:**

*Anthropic Path:*
- AGENT_MODEL_PROVIDER=anthropic
- ANTHROPIC_API_KEY=sk-ant-...

*Gemini Path:*
- AGENT_MODEL_PROVIDER=gemini
- GEMINI_API_KEY=...

*OpenAI Path:*
- AGENT_MODEL_PROVIDER=openai
- OPENAI_API_KEY=sk-...

**Required for Local Path:**
- AGENT_MODEL_PROVIDER=local
- AGENT_VISION_SERVER_URL=http://localhost:8080 (running vision model server)
- AGENT_TEXT_SERVER_URL=http://localhost:11434 (running Ollama or compatible)
- AGENT_VISION_MODEL=qwen3-vl (or other local model)
- AGENT_TEXT_MODEL=gemma2:9b (or other Ollama model)

**Secrets Location:**
- Environment variables (AGENT_* prefix)
- .env file (git-ignored, never committed)
- No secrets in code, config files, or version control

## Vision Model Coordinate Spaces

**Normalization Mapping:**
Located in `src/automation_agent/vision/coordinator.py`:

```python
COORDINATE_SPACES = {
    "molmo": "normalized_0_100",          # Divide by 100
    "molmo2": "normalized_0_1000",        # Divide by 1000
    "qwen3-vl": "normalized_0_1000",      # Divide by 1000
    "qwen2.5-vl": "normalized_0_1000",    # Divide by 1000
    "qwen2-vl": "normalized_0_1000",      # Divide by 1000
    "claude-sonnet-4-20250514": "pixel",  # Pixel coordinates
    "gpt-4.1": "pixel",                   # Pixel coordinates
    "gpt-4o": "pixel",                    # Pixel coordinates
    "gpt-5.4": "pixel",                   # Pixel coordinates
}
```

## Per-Step Model Routing

**Configuration Pattern:**
Each step type can route to a different provider/model via per-step overrides:

- `AGENT_PLANNING_MODEL` - plan() and replan() LLM calls
- `AGENT_GROUNDING_MODEL_PROVIDER` - find_element() vision calls
- `AGENT_VERIFICATION_MODEL` - verify_condition() visual verification
- `AGENT_SCREEN_DESCRIPTION_MODEL` - describe_screen() calls
- `AGENT_REFLECTION_MODEL` - async post-run reflection

**Format:** "provider:model" (e.g., "openai:gpt-5.4") or just provider name (uses provider's default model)

**Resolution:** `config.resolve_step_model(step)` returns (provider, model) tuple, falling back to global AGENT_MODEL_PROVIDER

---

*Integration audit: 2026-03-20*
