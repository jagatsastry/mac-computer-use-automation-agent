# LLM Comparison for Planning

**Date**: 2026-03-12
**Task**: Generate an action plan for "return my listerine on amazon"
**Prompt**: Standard `plan_from_prompt.md` template

## Cloud Models

### With skill context

| Model | Time | Tokens (in/out) | JSON Valid | Plan Quality | Goes to Orders? |
|-------|------|-----------------|-----------|-------------|-----------------|
| **Claude Sonnet 4** | 6.4s | 394/364 | Yes | Excellent | Yes (`/gp/your-orders`) |
| **Gemini 2.5 Flash** | 6.6s | 227/320 | Yes | Excellent | Yes (`/gp/css/order-history`) |
| **Gemini 3 Flash** (preview) | 7.3s | 227/307 | Yes | Excellent | Yes (`/gp/css/order-history`) |

### Without skill context

| Model | Time | Tokens (in/out) | JSON Valid | Plan Quality | Goes to Orders? |
|-------|------|-----------------|-----------|-------------|-----------------|
| **Claude Sonnet 4** | 7.5s | 332/464 | Yes | Good | No (homepage → Account → Orders) |
| **Gemini 2.5 Flash** | 8.4s | 306/325 | Yes | Good | No (homepage → Returns & Orders) |

Both cloud models produce correct return flows with skill context. Without it, they go to the homepage and navigate manually — workable but more steps and more fragile.

### Cloud Plan Details

**Claude with skill** (6 steps):
1. `activate_app(Safari)` → 2. `open_url(amazon.com/gp/your-orders)` → 3. `type_text(listerine)` → 4. `press_key(Return)` → 5. `click(Return or replace items)` → 6. `click(Return items option)`

**Gemini 2.5 Flash with skill** (4 steps):
1. `activate_app(Safari)` → 2. `open_url(order-history)` → 3. `type_text(listerine)` → 4. `press_key(enter)`

**Gemini 3 Flash with skill** (6 steps):
1. `open_url(order-history)` → 2. `type_text(listerine)` → 3. `press_key(enter)` → 4. `click(Return or replace items)` → 5. `click(Reason dropdown)` → 6. `click(Continue)`

Gemini 3 goes deeper into the return flow (selects reason, continues). Claude and Gemini 2.5 stop at the return button.

---

## Local Models (Ollama + llama.cpp, M3 Max)

| Model | Backend | Time | Tokens (comp) | tok/s | JSON Valid | Plan Quality | Usable? |
|-------|---------|------|---------------|-------|-----------|-------------|---------|
| **gemma2:9b** | Ollama (warm) | 9.2s | 196 | 21.3 | Yes* | Good | **Yes** |
| **gemma2:9b** | Ollama (cold) | 44.2s | 283 | 6.4 | Yes* | Good | Yes (warm only) |
| **gemma2:9b** | llama.cpp | 10.5s | 210 | 20.1 | Yes* | Good | Yes |
| **qwen3:4b** | Ollama | 108.7s | 4634 | 38.4 | No | Bad | **No** |
| **qwen3:4b** | llama.cpp | 79.2s | 3509 | 44.3 | No | Bad (0 chars) | **No** |
| **deepseek-r1:8b** | Ollama | 274.8s | 3575 | 13.0 | Yes | Good | **No** (too slow) |
| **deepseek-r1:8b** | llama.cpp | 267.9s | 3516 | 13.1 | No | Bad (0 chars) | **No** |
| **qwen3-vl** | Ollama | >120s | — | — | N/A | N/A | **No** (vision model) |

\* gemma2 wraps JSON in markdown fences — requires stripping.

### With Ollama Structured Output (format schema via /api/chat)

| Model | Time | Tokens | tok/s | JSON Valid | Steps | Usable? |
|-------|------|--------|-------|-----------|-------|---------|
| **qwen3:4b-instruct** | **9.4s** | 330 | 35.1 | Yes | 4 (rich detail) | **Yes** |
| **gemma2:9b** | 10.5s | 154 | 14.7 | Yes | 2 (sparse) | **Yes** |

Structured output eliminates markdown fences and guarantees valid JSON via GBNF grammar-constrained decoding. The `qwen3:4b-instruct-2507-q4_K_M` variant has **no `<think>` overhead** — 9.4s vs 108.7s for the default thinking variant. It also produces richer plans with detailed verify/on_fail fields.

### Local Plan Details

**gemma2:9b** (with skill, Ollama warm):
```json
{
  "steps": [
    {"action": "open_url", "params": {"url": "https://www.amazon.com/gp/css/order-history"}, "verify": "Orders page visible", "on_fail": "retry_different"},
    {"action": "type_text", "params": {"text": "listerine"}, "verify": "Matching order visible", "on_fail": "replan"}
  ]
}
```
Follows skill steps faithfully. Sparse (2 steps) but correct.

**qwen3:4b** (with skill):
```json
[
  {"action": "click", "element": "Account link in top right", "verify": "Account dropdown menu appears", "on_fail": "replan"},
  {"action": "click", "element": "Your Orders link in account dropdown", "verify": "Your Orders page visible", "on_fail": "replan"},
  {"action": "type_text", "text": "listerine", "verify": "Search bar for product name visible", "on_fail": "replan"},
  {"action": "click", "element": "Search button", "verify": "Order list with Listerine visible", "on_fail": "replan"}
]
```
Better multi-step reasoning, but: wrong JSON structure (bare array), wrong param format, 109s latency.

**deepseek-r1:8b** (with skill):
```json
{
  "steps": [
    {"action": "open_url", "params": {"url": "https://www.amazon.com/orders"}, "verify": "Orders page visible", "on_fail": "wait_for_user"},
    {"action": "type_text", "params": {"text": "listerine", "target": "search_field"}, "verify": "Matching order visible", "on_fail": "wait_for_user"}
  ]
}
```
Concise, follows skill exactly. But 275s latency makes it unusable.

---

## Cross-Model Comparison

| | Claude Sonnet 4 | Gemini 2.5 Flash | gemma2:9b (local) |
|--|----------------|-----------------|-------------------|
| **Latency** | 6-8s | 8-11s | 9-10s (warm) |
| **JSON** | Clean, valid | Clean, valid | Markdown fences |
| **Instruction following** | Excellent | Excellent | Good |
| **Skill adherence** | Follows closely | Follows + adds observe | Follows faithfully |
| **Cost** | ~$0.01/plan | ~$0.001/plan | $0 |

## Key Findings

### Reasoning models are unusable for planning

Both **qwen3:4b** and **deepseek-r1:8b** use mandatory `<think>` blocks:
1. **Extreme latency**: 79-275s vs 6-10s for non-reasoning models
2. **Context exhaustion**: Think tokens consume the output window, leaving 0 chars of content
3. **No benefit**: Desktop automation planning is pattern matching, not reasoning

### Ollama ≈ llama.cpp performance

Same GGUF backend, same Apple Silicon Metal acceleration. Negligible differences (gemma2: 21.3 vs 20.1 tok/s). Use Ollama for convenience.

### Cold start penalty

Ollama keeps models in memory ~5 minutes. Cold start adds 30-40s (gemma2: 9.2s warm → 44.2s cold). Pre-warm with a dummy request for interactive use.

### Skills matter more than model choice

Without skill context, all models (including Claude) produce suboptimal plans that navigate from the homepage. With skill context, even gemma2:9b goes directly to the orders page. Invest in skill library quality over model upgrades.

---

## Recommended Configurations

### Cloud (best quality)
```bash
# Option A: Claude (best instruction following, ~$0.01/plan)
AGENT_MODEL_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-key

# Option B: Gemini (cheapest cloud, ~$0.001/plan)
AGENT_MODEL_PROVIDER=gemini
GEMINI_API_KEY=your-key
AGENT_GEMINI_MODEL=gemini-2.5-flash
```

### Local (free, offline)
```bash
AGENT_MODEL_PROVIDER=local
AGENT_TEXT_SERVER_URL=http://localhost:11434
AGENT_TEXT_MODEL=gemma2:9b
```

### Optimal Local Model Split

| Task | Model | Server | Rationale |
|------|-------|--------|-----------|
| **Planning** | gemma2:9b | Ollama (11434) | Fast, follows instructions, valid JSON |
| **Screen description** | qwen3-vl | Ollama (11434) | VLM needed for image understanding |
| **Verification** | qwen3-vl | Ollama (11434) | VLM needed for yes/no visual checks |
| **Element grounding** | Molmo v1 | MLX (8091) | Best grounding accuracy (75% on ScreenSpot) |

### Grounding (always local — cloud models are bad at pixel coordinates)
```bash
AGENT_VISION_SERVER_URL=http://localhost:8091
AGENT_VISION_MODEL=molmo
```

---

## Next Steps: Models to Try

| Model | Why | Expected Improvement |
|-------|-----|---------------------|
| **qwen3.5:4b** | March 2026 SOTA — 9B scores 76.5 IFBench (beats GPT-5.2) | Best small model, 3.4GB, fast |
| **qwen2.5:7b-instruct** | Purpose-trained for JSON structured output | Better JSON (no markdown fences), similar speed |
| **gemma3:4b** | Next-gen, native function calling, 2x faster | 3-6s plans, native JSON |
| **phi4-mini** | 3.8B, 80-120 tok/s, ultra-fast | 2-4s plans (simple tasks) |

**Note on qwen3:4b**: The default Ollama tag points to the thinking-enabled variant. Use `qwen3:4b-instruct-2507-q4_K_M` for the non-thinking version.

### Ollama Structured Output (biggest potential improvement for local)

Add `"format": {"type": "object", ...}` to Ollama API calls for grammar-constrained decoding. Guarantees valid JSON regardless of model.

```python
payload = {
    "model": self.config.text_model,
    "messages": [...],
    "format": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string"},
                        "params": {"type": "object"},
                        "verify": {"type": "string"}
                    },
                    "required": ["action", "params", "verify"]
                }
            }
        },
        "required": ["steps"]
    }
}
```
