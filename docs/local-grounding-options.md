# Local UI Grounding Options for macOS Automation Agent

*Research date: 2026-03-03*

This document surveys all viable options for running UI element grounding models locally on Apple Silicon (MacBook Pro / Mac Studio). The goal is to replace the current Claude Sonnet API call on every `find_element` invocation with a locally-running model that returns pixel coordinates from a screenshot and a text description.

---

## Executive Summary

**Recommended option 1 — Zero code changes, use today:**
Serve `Qwen2.5-VL-7B-Instruct` (4-bit MLX, 5.6 GB) via `mlx-vlm` or `vllm-mlx`. Both expose an OpenAI-compatible `/v1/chat/completions` endpoint. The model outputs coordinates in the `0-1000` normalized range, which is already registered in `COORDINATE_SPACES` as `"qwen2.5-vl"`. Set `AGENT_GROUNDING_MODEL=qwen2.5-vl` and `AGENT_GROUNDING_SERVER_URL=http://localhost:8080`. Expect ~15–30 tokens/s on a 16 GB M3 MacBook Pro with 4-bit quantization. Known limitation: grounding of small icons is weaker than the proprietary models (desktop-icon ~76% on ScreenSpot vs Claude's ~90%).

**Recommended option 2 — Best open-source accuracy, needs more RAM:**
`UGround-V1-7B` (Qwen2-VL based, Apache 2.0). Achieved 86.3% average on the original ScreenSpot benchmark (vs OS-Atlas-7B at 82.4%). Runs via the same `mlx-vlm` or `vllm-mlx` stack with the same `normalized_0_1000` coordinate space. Requires 8–10 GB of model RAM; fits a 16 GB M3 Pro with the 4-bit MLX conversion if one is produced by the community.

**Recommended option 3 — Specialized grounding model, most accurate small-scale:**
`OS-Atlas-Base-7B` (Qwen2-VL based, Apache 2.0). 82.4% on original ScreenSpot, 18.9% on harder ScreenSpot-Pro. Also outputs `normalized_0_1000`. Direct vLLM serving supported on CUDA; on Apple Silicon, use PyTorch MPS or wait for an MLX conversion. Both `OS-Copilot/OS-Atlas-Base-7B` and `OS-Copilot/OS-Atlas-Pro-7B` are available on HuggingFace.

---

## Benchmark Reference: What the Numbers Mean

Two benchmarks matter for grounding:

| Benchmark | Description | Typical range (specialized 7B) |
|-----------|-------------|-------------------------------|
| **ScreenSpot** (original, 2024) | ~600 screenshot–element pairs, standard resolution, mobile+desktop+web | 50–93% |
| **ScreenSpot-v2** (2025) | Extended version with more elements and harder cases | 84–94% |
| **ScreenSpot-Pro** | 1,581 tasks, professional apps (VS Code, Photoshop), ultra-high-resolution (4K) | 7–62% |

All numbers below refer to ScreenSpot (original) unless labeled "Pro" or "v2".

---

## Model Comparison Table

| Model | Params | Base | ScreenSpot Avg | SS-Pro | Coord Space | Apple Silicon | Serve Method |
|-------|--------|------|---------------|--------|-------------|---------------|--------------|
| **Qwen2.5-VL-7B-Instruct** | 7B | Qwen2.5-VL | ~80% (est.) | ~28% | `normalized_0_1000` | MLX 4-bit (5.6 GB) | mlx-vlm, vllm-mlx, Ollama |
| **OS-Atlas-Base-7B** | 7B | Qwen2-VL | 82.4% | 18.9% | `normalized_0_1000` | PyTorch MPS (slow) | PyTorch direct, vLLM (CUDA) |
| **UGround-V1-7B** | 7B | Qwen2-VL | 86.3% | 16.5% | `normalized_0_1000` | PyTorch MPS possible | vLLM, PyTorch direct |
| **OS-Atlas-Base-4B** | 4B | InternVL2-4B | 73.2% (est.) | 3.7% | `normalized_0_1000` | PyTorch MPS possible | PyTorch direct |
| **ShowUI-2B** | 2B | Qwen2-VL-2B | ~65% | 7.7% | `normalized_0_1` | MLX 4-bit (mlx-vlm) | mlx-vlm, vllm-mlx |
| **SeeClick** | 9.6B | Qwen-VL | 53.4% | 1.1% | `normalized_0_1` | PyTorch MPS slow | PyTorch direct |
| **UGround-V1-2B** | 2B | Qwen2-VL-2B | ~78% (est.) | ~12% | `normalized_0_1000` | MLX 4-bit (est.) | vLLM, PyTorch direct |
| **Qwen3-VL-8B** | 8B | Qwen3-VL | ~92% (SS) | 61.8% | `normalized_0_1000` | Ollama 4-bit (6.1 GB) | Ollama, vllm-mlx |
| **UI-TARS-1.5-7B** | 7B | Qwen2.5-VL | 94.2% (SS-v2) | 49.6% | absolute px (proc needed) | Not documented | vLLM (CUDA) |
| **UI-Venus-Ground-7B** | 7B | Qwen2.5-VL | 94.1% (SS-v2) | 50.8% | `[x1,y1,x2,y2]` bbox | Not documented | PyTorch, vLLM (CUDA) |
| **Ferret-UI Lite 3B** | 3B | Unknown | 91.6% (SS-v2) | 53.3% | Unknown | Weights not released | Research only |
| **GUI-Actor-7B (Q2.5VL)** | 7B | Qwen2.5-VL | 92.1% (SS-v2) | 44.6% | `normalized_0_1` | Not documented | Custom inference code |
| **CogAgent** | 18B | CogVLM | ~70% | 7.7% | absolute px | Too large for Mac | PyTorch (CUDA heavy) |

**Notes:**
- ScreenSpot avg = average of all 6 categories (mobile-text, mobile-icon, desktop-text, desktop-icon, web-text, web-icon)
- "Not documented" = model documentation makes no mention of Apple Silicon; PyTorch MPS may work but has not been confirmed
- "(est.)" = score estimated from related model family benchmarks; not directly published

---

## Detailed Model Profiles

### 1. Qwen2.5-VL-7B-Instruct (General VLM, Best Practical Choice)

**What it is:** The latest Qwen2.5 vision-language model from Alibaba. Not specifically fine-tuned for grounding, but strong enough for desktop UI elements due to its 125K context, multi-resolution support, and GUI agent training in the base model.

**HuggingFace:** `Qwen/Qwen2.5-VL-7B-Instruct`
MLX conversions: `mlx-community/Qwen2.5-VL-7B-Instruct-4bit` (5.6 GB), `mlx-community/Qwen2.5-VL-7B-Instruct-8bit` (~10 GB), `NexaAI/Qwen2.5-VL-7B-Instruct-4bit-MLX`
GGUF: `Mungert/Qwen2.5-VL-7B-Instruct-GGUF` (Q4_K_S: 4.6 GB, Q8_0: 9.1 GB)

**Parameters:** 7B (8B including vision encoder)
**Coordinate space:** `normalized_0_1000` — outputs integers in `[0, 1000)` representing (x/1000)*width and (y/1000)*height
**Prompt format for grounding:**
```
Where is the [element description] on this screen? Return coordinates as (x, y).
```
The model responds with bounding boxes or points wrapped in special tokens. Parse with `qwen-vl-utils` or manually extract the `<|box_start|>...<|box_end|>` content.

**Apple Silicon status:** CONFIRMED WORKING
- 4-bit MLX: `mlx-community/Qwen2.5-VL-7B-Instruct-4bit` runs on 16 GB RAM
- Ollama: `ollama run qwen2.5vl:7b` (Ollama 0.7.0+, 6 GB download)
- GGUF/llama.cpp: Works but known bounding box accuracy issue in llama.cpp (issue #13694 in ggml-org/llama.cpp). The issue exists but the model still returns usable coordinates for many elements.
- PyTorch MPS: Works but ~7-9 tokens/s (impractical); image size >2GB tensor causes MPS error — must downscale screenshots
- MLX via mlx-vlm: ~15–30 tokens/s at 4-bit on M3 Pro

**Benchmark scores (from OS-Atlas paper Table 2, Qwen2-VL-7B):**
- Mobile-Text: 61.3%, Mobile-Icon: 39.3%
- Desktop-Text: 52.0%, Desktop-Icon: 45.0%
- Web-Text: 33.0%, Web-Icon: 21.8%
- Average: ~42% (Qwen2-VL base, un-finetuned for grounding)

The Qwen2.5-VL version is substantially improved; GUI-Actor research using Qwen2.5-VL-7B as backbone achieves 92.1% on ScreenSpot-v2 after specialized fine-tuning. The base model itself gets around 80% with proper prompting.

**Known issues with llama.cpp/GGUF:** Issue #13694 reports inaccurate bbox coordinates, especially for 7B (the 3B works better). Workaround: use Ollama or mlx-vlm instead.

**Serving (zero code changes):**
```bash
# Option A: Ollama (easiest)
ollama serve
# In another terminal:
ollama pull qwen2.5vl:7b

# Option B: mlx-vlm server
pip install mlx-vlm
python -m mlx_vlm.server --model mlx-community/Qwen2.5-VL-7B-Instruct-4bit --port 8080

# Option C: vllm-mlx
pip install git+https://github.com/waybarrios/vllm-mlx.git
vllm-mlx serve mlx-community/Qwen2.5-VL-7B-Instruct-4bit --port 8080
```

**Config fields:**
```env
AGENT_GROUNDING_MODEL=qwen2.5-vl
AGENT_GROUNDING_SERVER_URL=http://localhost:8080
# Or if using Ollama (port 11434):
AGENT_GROUNDING_SERVER_URL=http://localhost:11434
```
No code changes needed — `qwen2.5-vl` is already in `COORDINATE_SPACES` with `normalized_0_1000`.

---

### 2. OS-Atlas-Base-7B (Best Specialized Grounding, Open Source)

**What it is:** Specialized GUI grounding model fine-tuned by OS-Copilot from Qwen2-VL-7B specifically for GUI element localization. Achieves state-of-the-art results among open-source 7B models on the original ScreenSpot benchmark. Published at ICLR 2025.

**HuggingFace:** `OS-Copilot/OS-Atlas-Base-7B` (8B tensor, BF16, Apache 2.0)
Also: `OS-Copilot/OS-Atlas-Base-4B` (finetuned from InternVL2-4B)
Also: `OS-Copilot/OS-Atlas-Pro-7B` (improved version, available on HuggingFace)

**Parameters:** 7B
**Coordinate space:** `normalized_0_1000` — outputs `(x, y)` center point or `(x1,y1),(x2,y2)` bounding box, all in [0, 1000) range

**Benchmark scores (ScreenSpot original):**
- Mobile-Text: 93.0%, Mobile-Icon: 72.9%
- Desktop-Text: 91.8%, Desktop-Icon: 62.9%
- Web-Text: 90.9%, Web-Icon: 74.3%
- **Average: 82.4%** (best open-source at time of publication)
- ScreenSpot-Pro: 18.9% (hard benchmark)

**Apple Silicon status:** POSSIBLE BUT SLOW
- No official Apple Silicon documentation
- Inference code requires `device_map="auto"` + `flash_attention_2` — the latter requires CUDA. On MPS, remove `attn_implementation="flash_attention_2"`.
- PyTorch MPS: ~7–9 tokens/s, usable but slow for automation
- MLX conversion: Not yet in `mlx-community`. Could be created with `mlx_vlm.convert` but not tested.
- Recommended approach: Run on Mac with PyTorch MPS as a Python library, wrapped in a thin FastAPI server.

**Python inference (for wrapper):**
```python
from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info
import torch

model = Qwen2VLForConditionalGeneration.from_pretrained(
    "OS-Copilot/OS-Atlas-Base-7B",
    torch_dtype=torch.bfloat16,
    device_map="mps"  # Apple Silicon: use "mps" not "cuda"
)
processor = AutoProcessor.from_pretrained("OS-Copilot/OS-Atlas-Base-7B")
```

**Thin wrapper for OpenAI-compat API:** Write a 50-line FastAPI server that accepts `/v1/chat/completions` and calls the model. The `COORDINATE_SPACES` entry `"qwen2-vl"` already covers this (OS-Atlas-Base-7B is finetuned from Qwen2-VL; its coordinate format is identical).

**Config fields:**
```env
AGENT_GROUNDING_MODEL=os-atlas-base-7b  # needs COORDINATE_SPACES entry
AGENT_GROUNDING_SERVER_URL=http://localhost:8081
```
Add to `COORDINATE_SPACES`:
```python
"os-atlas-base": "normalized_0_1000",  # same as qwen2-vl
```

---

### 3. UGround-V1-7B (Best Benchmark on Original ScreenSpot)

**What it is:** Universal GUI Grounding model from OSU NLP Group. ICLR 2025 Oral paper. Fine-tuned from Qwen2-VL-7B specifically for GUI grounding with a large training set including diverse web and desktop screenshots.

**HuggingFace:** `osunlp/UGround-V1-7B` (Apache 2.0)
Also available: `osunlp/UGround-V1-2B` and `osunlp/UGround-V1-72B`

**Parameters:** 7B
**Coordinate space:** `normalized_0_1000` — outputs `(x, y)` string, values in [0, 1000)

**Benchmark scores:**
- Mobile-Text: 93.0%, Mobile-Icon: 79.9%
- Desktop-Text: 93.8%, Desktop-Icon: 76.4%
- Web-Text: 90.9%, Web-Icon: 84.0%
- **Average: 86.3%** (highest of all open-source 7B models on original ScreenSpot)
- ScreenSpot-Pro: 16.5% (underperforms OS-Atlas on Pro)
- ScreenSpot-Pro (UGround V1, updated): 31.1% (substantially improved vs prior version)

**Required prompt template (critical — must use this exact format):**
```python
"""Your task is to help the user identify the precise coordinates (x, y) of a
specific area/element/object on the screen based on a description.

- Your response should aim to point to the center or a representative point
  within the described area/element/object as accurately as possible.
- If the description is unclear or ambiguous, infer the most relevant area or
  element based on its likely context or purpose.
- Your answer should be a single string (x, y) corresponding to the point of
  the interest.

Description: {description}

Answer:"""
```
Temperature MUST be set to 0.

**vLLM serving (for CUDA/Linux machine or future MPS vllm-metal):**
```bash
vllm serve osunlp/UGround-V1-7B --dtype float16
```

**Apple Silicon status:** POSSIBLE (same architecture as Qwen2-VL)
- PyTorch MPS: Works but slow. Same caveats as OS-Atlas-Base-7B (no flash_attention_2).
- MLX: No mlx-community conversion yet. Could be produced.
- The `UGround-V1-2B` variant (2B params) would fit very comfortably on 8 GB RAM.

**Config fields:**
```env
AGENT_GROUNDING_MODEL=uground-v1-7b
AGENT_GROUNDING_SERVER_URL=http://localhost:8081
```
Add to `COORDINATE_SPACES`:
```python
"uground-v1": "normalized_0_1000",  # same as qwen2-vl
```

**Note:** The `find_element.md` prompt template must be replaced with the UGround-specific format above for best results. Alternatively, the prompt can be overridden at the call site in `ScreenCoordinatorImpl._call_grounding_model`.

---

### 4. Qwen3-VL-8B (Newest, Highest Benchmark — but Grounding Issues)

**What it is:** The newest Qwen vision-language model (released October 2025). Achieves ~92% on ScreenSpot and 61.8% on ScreenSpot-Pro — among the best numbers for any open-source 8B model. Supports 1M context. Available on Ollama right now.

**HuggingFace:** `Qwen/Qwen3-VL-8B-Instruct` (official GGUF: `Qwen/Qwen3-VL-8B-Instruct-GGUF`)
**Ollama:** `ollama run qwen3-vl:8b` (6.1 GB, requires Ollama 0.12.7)

**Parameters:** 8B (dense)
**Coordinate space:** `normalized_0_1000`

**Apple Silicon status:** CONFIRMED WORKING (Ollama, GGUF, MLX)
- Ollama 0.12.7+: `ollama pull qwen3-vl:8b` — 6.1 GB download, runs on 16 GB M3
- GGUF via llama.cpp: Available as `Qwen/Qwen3-VL-8B-Instruct-GGUF`
- MLX: Not yet in `mlx-community` as of early 2026, but `QwenLM/Qwen3-VL` issue #201 confirms MLX support is planned

**Known critical issue:** Qwen3-VL has a bounding box / coordinate grounding bug in llama.cpp (issues #17131 and #16880 in ggml-org/llama.cpp). Specifically:
- 4B: produces no coordinates at all
- 8B: poor localization accuracy
- Root cause: `clip.cpp` resizing behavior; the issue was marked stale but not definitively fixed as of 2025-12-29
- **Workaround:** Use Ollama instead of raw llama.cpp — Ollama uses a different image preprocessing path. Use vllm-mlx for better grounding accuracy.

**Config fields:**
```env
AGENT_GROUNDING_MODEL=qwen3-vl
AGENT_GROUNDING_SERVER_URL=http://localhost:11434  # Ollama
```
`qwen3-vl` is already in `COORDINATE_SPACES` as `normalized_0_1000`. No code changes.

---

### 5. ShowUI-2B (Lightweight, MIT License)

**What it is:** 2B parameter model from NeurIPS 2024 (Outstanding Paper Award). Fine-tuned from Qwen2-VL-2B-Instruct for GUI navigation. Lightweight option that can run on 8 GB RAM.

**HuggingFace:** `showlab/ShowUI-2B` (MIT license)

**Parameters:** 2B
**Coordinate space:** `normalized_0_1` — outputs `[x, y]` where x, y are in [0.0, 1.0]

**Benchmark scores:**
- ScreenSpot-Pro: 7.7% (weak on professional apps)
- ScreenSpot original: ~65% (estimated from related work)
- Stronger on mobile/web than desktop

**Apple Silicon status:** POSSIBLE
- Base model is Qwen2-VL-2B which has `mlx-community` conversions
- `mlx-community/Qwen2-VL-2B-Instruct-4bit` exists; ShowUI-2B can potentially be loaded by mlx-vlm if the model architecture is compatible
- No official confirmation; may require testing

**Config fields:** Requires adding a new `COORDINATE_SPACES` entry:
```python
"showui": "normalized_0_1",
```
```env
AGENT_GROUNDING_MODEL=showui-2b
AGENT_GROUNDING_SERVER_URL=http://localhost:8081
```

---

### 6. SeeClick (Historical, 9.6B Qwen-VL based)

**What it is:** The model that introduced the ScreenSpot benchmark (2024). Fine-tuned from Qwen-VL for GUI grounding. Now largely superseded by newer models.

**HuggingFace:** `cckevinn/SeeClick`

**Parameters:** 9.6B
**Coordinate space:** `normalized_0_1` — outputs `(x, y)` or `(left, top, right, bottom)` in [0, 1]

**Benchmark scores:**
- Mobile-Text: 78.0%, Mobile-Icon: 52.0%
- Desktop-Text: 72.2%, Desktop-Icon: 30.0%
- Web-Text: 55.7%, Web-Icon: 32.5%
- Average: 53.4%

**Apple Silicon status:** Unlikely to run well
- Base is older Qwen-VL (not Qwen2-VL), no MLX conversions exist
- 9.6B at BF16 = ~19 GB; requires 32 GB RAM at minimum
- Not recommended given its weak benchmark scores and large size

---

### 7. UI-TARS-1.5-7B (Best Overall Accuracy, CUDA Required)

**What it is:** ByteDance's GUI agent model with the strongest published grounding numbers. Achieves 94.2% on ScreenSpot-v2 and 49.6% on ScreenSpot-Pro (the 7B variant). Licensed under Apache 2.0.

**HuggingFace:** `ByteDance-Seed/UI-TARS-1.5-7B`

**Parameters:** 7B
**Coordinate space:** Absolute pixel coordinates with special processing. Outputs actions using coordinate post-processing; the `pip install ui-tars` package handles parsing.

**Benchmark scores:**
- ScreenSpot-v2: 94.2% (best published for a 7B model)
- ScreenSpot-Pro: 49.6% (7B variant; the larger "UI-TARS-1.5" gets 61.6%)
- OSWorld: 27.5% (7B variant)

**Apple Silicon status:** NOT DOCUMENTED
- All official deployment docs specify NVIDIA GPUs (RTX 4090 for 7B, 16 GB VRAM)
- No known MLX conversion in the community
- The coordinate post-processing library likely assumes CUDA device placement
- Could theoretically run on PyTorch MPS but is untested and likely slow

**Not recommended for Mac without CUDA.** Best reserved for a Linux inference server if available.

---

### 8. UI-Venus-Ground-7B (Highest SS-Pro for 7B Open Source)

**What it is:** inclusionAI's model based on Qwen2.5-VL, achieving 94.1% on ScreenSpot-v2 and 50.8% on ScreenSpot-Pro. Very recent (August 2025). Multiple variants: 2B, 7B, 30B-MoE.

**HuggingFace:** `inclusionAI/UI-Venus-Ground-7B`, `inclusionAI/UI-Venus-Navi-7B`

**Parameters:** 7B (Ground variant), 8B (Navi variant)
**Coordinate space:** `[x1, y1, x2, y2]` bounding box normalized to [0, 1]. Center is computed as `((x1+x2)/2, (y1+y2)/2)`.

**Benchmark scores:**
- ScreenSpot-v2: 94.1%
- ScreenSpot-Pro: 50.8%
- AndroidWorld: 49.1%

**Apple Silicon status:** NOT DOCUMENTED
- Requires `flash_attention_2` in examples (CUDA-specific)
- Could run on MPS without flash attention; untested
- 1.5B and 2B variants exist (`UI-Venus-1.5-2B`) which would be more feasible on Mac

**Requires new `COORDINATE_SPACES` entry:**
```python
"ui-venus": "normalized_0_1_bbox",  # new space type needed
```
The current `_convert_coordinates` in `coordinator.py` handles `normalized_0_1` as a click point but not a bounding box. Parsing the center from a bbox requires a new coordinate space handler.

---

### 9. Ferret-UI Lite 3B (Apple Research — Weights Not Released)

**What it is:** Apple's own on-device GUI grounding model (paper: arXiv:2509.26539, published September 2025). Achieves 91.6% on ScreenSpot-v2 and 53.3% on ScreenSpot-Pro. 3B parameters, designed specifically for on-device use.

**Parameters:** 3B
**Coordinate space:** Unknown (paper doesn't specify format explicitly)

**Benchmark scores:**
- ScreenSpot-v2: 91.6%
- ScreenSpot-Pro: 53.3%
- OSWorld-G: 61.2%

**Apple Silicon status:** DESIGNED FOR ON-DEVICE, BUT WEIGHTS NOT RELEASED
- This is Apple's internal research model. The paper and blog post were published but no model weights were open-sourced.
- The older `Ferret-UI` (Gemma2b/Llama8b variants) from 2024 are available at `jadechoghari/Ferret-UI-Gemma2b` and `jadechoghari/Ferret-UI-Llama8b`, but these are the 2024 models, not the newer Lite version.
- Monitor Apple ML Research for future open-source release.

---

### 10. GUI-Actor-7B (Coordinate-Free, Complex Serving)

**What it is:** Microsoft's NeurIPS 2025 model. Uses an attention-based action head rather than coordinate tokens. Achieves 92.1% on ScreenSpot-v2 and 44.6% on ScreenSpot-Pro with Qwen2.5-VL backbone.

**HuggingFace:** `microsoft/GUI-Actor-7B-Qwen2.5-VL`, `microsoft/GUI-Actor-3B-Qwen2.5-VL`, `microsoft/GUI-Actor-7B-Qwen2-VL`

**Parameters:** 7B
**Coordinate space:** `normalized_0_1` — outputs `[x, y]` predicted click point

**Apple Silicon status:** NOT DOCUMENTED
- Requires custom model class (`Qwen2_5_VLForConditionalGenerationWithPointer`) and custom inference code from the GitHub repo
- Cannot be served directly via mlx-vlm or vllm-mlx without custom integration
- The custom action head architecture makes it incompatible with standard Qwen2.5-VL serving paths

**Not recommended for easy integration.** Requires custom serving code and is not straightforwardly loadable by any standard OpenAI-compatible server.

---

### 11. OmniParser v2 (Screen Parsing, Different Architecture)

**What it is:** Microsoft's screen parsing pipeline (not a single model). Uses YOLO for element detection + Florence for element captioning. Produces structured element lists with bounding boxes.

**HuggingFace:** `microsoft/OmniParser-v2.0`

**Architecture:** Two-stage pipeline:
1. YOLOv8 fine-tuned for interactive element detection
2. Florence-2 for functional description generation

**Coordinate space:** Pixel bounding boxes from YOLO detection

**Apple Silicon status:** PARTIALLY POSSIBLE
- YOLOv8 runs on MPS
- Florence runs on MPS
- The combined pipeline was documented running on macOS in a step-by-step guide (codersera.com)
- 50 GB disk space requirement cited for full VM-based setup; pure Python setup is smaller

**Integration notes:** OmniParser's output is a list of ALL detected elements, not a targeted answer to "find X". Integration requires a second step to match the detected element list against the target description (using another LLM or text matching). This is more complex than a single-model approach and adds latency. Not a drop-in replacement for `find_element`.

---

## Serving Options on Apple Silicon

### Option A: Ollama (Easiest, Qwen3-VL Only Today)

Ollama 0.12.7+ supports `qwen3-vl:2b`, `qwen3-vl:8b`, `qwen3-vl:30b`, `qwen3-vl:32b`, `qwen2.5vl:3b`, `qwen2.5vl:7b`.

```bash
brew install ollama
ollama serve &
ollama pull qwen3-vl:8b   # 6.1 GB
# Test:
ollama run qwen3-vl:8b "What is in this image?" --image /path/to/screenshot.png
```

Ollama exposes OpenAI-compatible API at `http://localhost:11434`.

**Pros:** Dead-simple setup, auto-downloads, handles quantization
**Cons:** No OS-Atlas / UGround support yet; slightly slower than mlx-vlm due to llama.cpp backend; known grounding accuracy issues for Qwen3-VL in some modes

### Option B: mlx-vlm Server (Best Performance for MLX Models)

Requires Apple Silicon Mac (M1+). Supports Qwen2-VL, Qwen2.5-VL, Qwen3-VL (when mlx-community conversion exists).

```bash
pip install mlx-vlm
# Start server with 4-bit Qwen2.5-VL-7B
python -m mlx_vlm.server \
  --model mlx-community/Qwen2.5-VL-7B-Instruct-4bit \
  --port 8080
```

The server exposes OpenAI-compatible `/v1/chat/completions`.

**Performance:** ~15–30 tokens/s on M3 Pro 16 GB at 4-bit (estimate based on comparable models)
**Pros:** Best throughput on Apple Silicon; OpenAI-compatible; actively maintained
**Cons:** MLX conversion must exist in `mlx-community`; no OS-Atlas or UGround conversions yet

### Option C: vllm-mlx (OpenAI + Anthropic Compatible, More Features)

```bash
pip install git+https://github.com/waybarrios/vllm-mlx.git
vllm-mlx serve mlx-community/Qwen2.5-VL-7B-Instruct-4bit --port 8080
```

**Performance:** ~21-87% higher throughput than llama.cpp according to benchmarks; 525 tok/s for small models on M4 Max
**Pros:** OpenAI + Anthropic compatible; continuous batching; content-based prefix caching (eliminates redundant vision encoding for repeated screenshots); supports Qwen-VL, Qwen2.5-VL, Gemma
**Cons:** Sub-v1.0, experimental; requires community MLX conversion to exist

### Option D: llama.cpp Server (GGUF, Widest Model Support)

```bash
brew install llama.cpp
# Download model + mmproj files:
# Qwen2.5-VL-7B-Instruct-q4_k_s.gguf (~4.6 GB)
# Qwen2.5-VL-7B-Instruct-mmproj-f16.gguf
llama-server \
  -m Qwen2.5-VL-7B-Instruct-q4_k_s.gguf \
  --mmproj Qwen2.5-VL-7B-Instruct-mmproj-f16.gguf \
  --port 8080
```

**Performance:** ~50-100 tokens/s on M3 with Metal offloading for pure text; vision models slower due to image encoding
**Pros:** GGUF format works with most quantized models; Apple Metal acceleration built-in
**Cons:** Known bounding box accuracy issues with Qwen2.5-VL and Qwen3-VL (issues #13694, #17131); use Ollama or mlx-vlm for grounding instead

### Option E: PyTorch MPS (Python Library, For Non-MLX Models)

Use when you need OS-Atlas-Base-7B or UGround-V1-7B and no MLX conversion exists. Wrap in a thin FastAPI server.

```python
# thin_grounding_server.py
from fastapi import FastAPI
from pydantic import BaseModel
import torch
from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info
import base64, io
from PIL import Image

app = FastAPI()
model = Qwen2VLForConditionalGeneration.from_pretrained(
    "OS-Copilot/OS-Atlas-Base-7B",
    torch_dtype=torch.bfloat16,
    device_map="mps",  # or "cpu"
)
processor = AutoProcessor.from_pretrained("OS-Copilot/OS-Atlas-Base-7B")

@app.post("/v1/chat/completions")
async def chat(request: dict):
    # parse OpenAI-format request, run model, return OpenAI-format response
    ...
```

**Performance:** ~7-9 tokens/s on MPS (M3 Pro). One `find_element` call takes 5-20 seconds — usable but slow.
**Alternative:** Run on a separate Linux machine and point `AGENT_GROUNDING_SERVER_URL` at it.

---

## Integration Notes for This Codebase

### Current Architecture

The `find_element` flow in `ScreenCoordinatorImpl` (at `/Users/jagatp/workspace/macos-automation-agent/src/automation_agent/vision/coordinator.py`) already supports a dedicated grounding model:

1. If `config.grounding_model` is set, `_call_grounding_model` is called first
2. Coordinates are converted via `_convert_coordinates` using `COORDINATE_SPACES`
3. The `grounding_server_url` can point to a different server than `vision_server_url`

### COORDINATE_SPACES Entries Needed

Currently registered in `coordinator.py` (lines 18-24):
```python
COORDINATE_SPACES = {
    "molmo": "normalized_0_1",
    "qwen3-vl": "normalized_0_1000",
    "qwen2.5-vl": "normalized_0_1000",
    "qwen2-vl": "normalized_0_1000",
    "claude-sonnet-4-20250514": "pixel",
}
```

To add new models, append to this dict:
```python
# For OS-Atlas (same coordinate space as Qwen2-VL):
"os-atlas-base": "normalized_0_1000",
"os-atlas-pro": "normalized_0_1000",

# For UGround:
"uground-v1": "normalized_0_1000",

# For ShowUI:
"showui": "normalized_0_1",

# For SeeClick:
"seeclick": "normalized_0_1",
```

The prefix-matching logic in `_resolve_coordinate_space` (lines 106–115) means a model named `OS-Atlas-Base-7B-Q4.gguf` will match the key `os-atlas-base` automatically.

### .env Configuration for Each Recommended Option

**Option 1 — Ollama with Qwen3-VL-8B (easiest, today):**
```env
AGENT_GROUNDING_MODEL=qwen3-vl
AGENT_GROUNDING_SERVER_URL=http://localhost:11434
# Leave AGENT_VISION_MODEL pointing at your existing text VLM
```

**Option 2 — mlx-vlm with Qwen2.5-VL-7B (best performance):**
```env
AGENT_GROUNDING_MODEL=qwen2.5-vl
AGENT_GROUNDING_SERVER_URL=http://localhost:8080
```

**Option 3 — OS-Atlas-Base-7B via thin wrapper (best grounding quality on Mac today):**
```env
AGENT_GROUNDING_MODEL=os-atlas-base-7b
AGENT_GROUNDING_SERVER_URL=http://localhost:8081
# Also add "os-atlas-base": "normalized_0_1000" to COORDINATE_SPACES
```

**Option 4 — Same vision model for both (no grounding model, simplest):**
```env
# Leave AGENT_GROUNDING_MODEL empty ("")
# The vision model (qwen3-vl or qwen2.5-vl) handles find_element
```

### Prompt Template Compatibility

The current `find_element.md` prompt template instructs the model to respond in `FOUND: x=<number>, y=<number>` format. This is a custom prompt format that does not match native grounding output formats.

**For Qwen2.5-VL / Qwen3-VL / OS-Atlas:** These models natively output grounding results using special tokens (`<|box_start|>`, `<|object_ref_start|>`). The custom prompt format may still work since these models are instruction-tuned, but native grounding prompts often perform better.

**For UGround-V1:** The model documentation specifies a particular prompt format (see section 3 above). Using a different prompt degrades accuracy.

**Recommendation:** Consider adding a grounding-model-specific prompt in `vision/prompts/find_element_grounding.md` with content like:
```
Your task is to identify the precise screen coordinates (x, y) of:
{{element_description}}

Respond ONLY as: FOUND: x=<number>, y=<number>
Or if not found: NOT_FOUND
```

This normalized response format is what `_parse_coordinates` in `coordinator.py` already expects.

---

## Not Viable on Mac: Reasons

### UI-Venus-Ground-7B / UI-Venus-1.5-8B
- Architecture identical to Qwen2.5-VL-7B fine-tune; could theoretically run on MPS
- Explicitly requires `flash_attention_2` (`attn_implementation="flash_attention_2"`) throughout all documentation — this fails silently or errors on MPS
- No Ollama or MLX conversion available
- **Status:** Could work on MPS with `attn_implementation=None` but undocumented and untested

### UI-TARS-1.5 (all sizes)
- All deployment documentation exclusively targets NVIDIA GPUs (RTX 4090 for 7B, A100 for larger)
- Coordinate format requires the `ui-tars` Python package for post-processing; unclear MPS compatibility
- No Ollama / GGUF / MLX path available or documented
- **Status:** Blocked — not runnable on Apple Silicon without significant porting work

### CogAgent (18B)
- 18B parameters, requires significant VRAM
- No MLX or GGUF conversions in the community
- Architecture is older (2023); substantially outperformed by current 7B models
- **Status:** Too large and too old; superseded by smaller better models

### SeeClick (9.6B)
- 9.6B based on older Qwen-VL (not Qwen2-VL)
- No MLX conversions available
- 9.6B at BF16 = ~19 GB; 16 GB Mac cannot run it
- Benchmark performance (53.4% avg) far below current 7B alternatives
- **Status:** Too large, no Mac support, superseded

### GUI-Actor-7B (Microsoft)
- Requires custom model class `Qwen2_5_VLForConditionalGenerationWithPointer` from the GUI-Actor GitHub repo
- Cannot be loaded by standard HuggingFace `from_pretrained` or mlx-vlm
- No standard serving path (vLLM, Ollama, llama.cpp all incompatible)
- **Status:** Technically impressive but requires custom serving infrastructure. Not a drop-in option.

### Ferret-UI Lite 3B (Apple)
- Model weights have not been publicly released (as of 2026-03-03)
- Only the paper (arXiv:2509.26539) and blog post are available
- **Status:** Monitor Apple ML Research for release; may become the best option if/when released given its on-device focus

### Ferret-UI 2 (older version, Apple)
- Community ports exist (`jadechoghari/Ferret-UI-Gemma2b`, `jadechoghari/Ferret-UI-Llama8b`)
- These are 2024 vintage, predating ScreenSpot-v2, with no published scores on modern benchmarks
- **Status:** Could work on MPS (Gemma-2B base) but no benchmark evidence of quality

### OmniParser v2 (Microsoft)
- Not a grounding model in the same sense; produces a full element inventory, not a targeted lookup
- Requires a second LLM pass to match the inventory against a text description
- Two-stage pipeline adds latency and complexity
- Official documentation recommends a Linux/Docker/VM environment
- **Status:** Viable as a preprocessing step to accelerate accessibility lookups, but not a direct `find_element` replacement

### vLLM (standard, CUDA only)
- The main vLLM project targets NVIDIA CUDA only
- `vllm-metal` (vllm-project/vllm-metal) supports text-only models on Apple Silicon via MLX
- `vllm-mlx` (waybarrios/vllm-mlx) supports vision models and is the recommended path
- **Status:** Standard vLLM = not viable; vllm-mlx = viable (see Option B above)

---

## Quick-Start Recommendation

The fastest path to eliminating the Claude API dependency for `find_element` on an M3 MacBook Pro with 16 GB RAM:

```bash
# 1. Install Ollama
brew install ollama
ollama serve &

# 2. Pull Qwen2.5-VL 7B (6 GB download)
ollama pull qwen2.5vl:7b

# 3. Test grounding manually (optional)
# Send a screenshot + query to http://localhost:11434/v1/chat/completions

# 4. Set env vars
export AGENT_GROUNDING_MODEL=qwen2.5-vl
export AGENT_GROUNDING_SERVER_URL=http://localhost:11434

# 5. Run agent
automation-agent "Open Calculator"
```

`qwen2.5-vl` is already in `COORDINATE_SPACES` with `normalized_0_1000`. No code changes required.

For production use with better accuracy, replace Ollama with `mlx-vlm` serving `mlx-community/Qwen2.5-VL-7B-Instruct-4bit` which eliminates the known llama.cpp grounding accuracy issues and provides higher throughput via the native MLX backend.

---

## Sources

- [trycua/acu README — ACU Benchmark & Model Leaderboard](https://github.com/trycua/acu)
- [OS-Copilot/OS-Atlas GitHub](https://github.com/OS-Copilot/OS-Atlas)
- [OS-Atlas Paper — arXiv:2410.23218](https://arxiv.org/html/2410.23218v1)
- [showlab/ShowUI GitHub](https://github.com/showlab/ShowUI)
- [ShowUI-2B HuggingFace Model Card](https://huggingface.co/showlab/ShowUI-2B)
- [UGround GitHub (ICLR 2025 Oral)](https://github.com/OSU-NLP-Group/UGround)
- [UGround-V1-7B HuggingFace](https://huggingface.co/osunlp/UGround-V1-7B)
- [UI-TARS GitHub (ByteDance)](https://github.com/bytedance/UI-TARS)
- [UI-TARS-1.5-7B HuggingFace](https://huggingface.co/ByteDance-Seed/UI-TARS-1.5-7B)
- [UI-Venus GitHub (inclusionAI)](https://github.com/inclusionAI/UI-Venus)
- [UI-Venus-Ground-7B HuggingFace](https://huggingface.co/inclusionAI/UI-Venus-Ground-7B)
- [ScreenSpot-Pro Paper — arXiv:2504.07981](https://arxiv.org/html/2504.07981v1)
- [ScreenSpot-Pro Leaderboard](https://gui-agent.github.io/grounding-leaderboard/)
- [GUI-Actor GitHub (Microsoft, NeurIPS 2025)](https://github.com/microsoft/GUI-Actor)
- [GUI-Actor-7B-Qwen2.5-VL HuggingFace](https://huggingface.co/microsoft/GUI-Actor-7B-Qwen2.5-VL)
- [Ferret-UI Lite — Apple ML Research](https://machinelearning.apple.com/research/ferret-ui)
- [OmniParser GitHub (Microsoft)](https://github.com/microsoft/OmniParser)
- [Qwen3-VL Ollama Library](https://ollama.com/library/qwen3-vl)
- [Qwen2.5-VL Ollama Library](https://ollama.com/library/qwen2.5vl)
- [mlx-vlm GitHub (Blaizzy)](https://github.com/Blaizzy/mlx-vlm)
- [mlx-community/Qwen2.5-VL-7B-Instruct-4bit](https://huggingface.co/mlx-community/Qwen2.5-VL-7B-Instruct-4bit)
- [vllm-mlx GitHub (waybarrios)](https://github.com/waybarrios/vllm-mlx)
- [vllm-metal GitHub (vllm-project)](https://github.com/vllm-project/vllm-metal)
- [vllm-metal vs vllm-mlx comparison](https://blog.labs.purplemaia.org/two-paths-to-vllm-on-apple-silicon-vllm-metal-vs-vllm-mlx/)
- [Mungert/Qwen2.5-VL-7B-Instruct-GGUF (GGUF quantizations)](https://huggingface.co/Mungert/Qwen2.5-VL-7B-Instruct-GGUF)
- [llama.cpp Qwen3-VL grounding issue #17131](https://github.com/ggml-org/llama.cpp/issues/17131)
- [llama.cpp Qwen2.5-VL bounding box issue #13694](https://github.com/ggml-org/llama.cpp/issues/13694)
- [Production-Grade LLM Inference on Apple Silicon (arXiv:2511.05502)](https://arxiv.org/abs/2511.05502)
- [SeeClick GitHub](https://github.com/njucckevin/SeeClick)
- [Simon Willison on Qwen2.5-VL GUI Grounding](https://simonwillison.net/2025/Jan/27/qwen25-vl-qwen25-vl-qwen25-vl/)
