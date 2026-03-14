# Grounding Benchmark How-To

Benchmark visual grounding (element finding) accuracy across different vision model backends using the ScreenSpot dataset.

## Prerequisites

1. **Python 3.11+** with the project virtual environment activated.

2. **Required packages** (install via `pip install -e ".[dev]"`):
   - `pyarrow` -- for loading the ScreenSpot Parquet files (falls back to curated JSON if unavailable)
   - `Pillow` -- for reading image dimensions
   - `anthropic` -- only needed if benchmarking the `claude-sonnet` backend

3. **Backend servers** (at least one must be reachable):
   - **claude-sonnet**: Set `ANTHROPIC_API_KEY` environment variable and install the `anthropic` package.
   - **qwen2.5-vl-ollama**: Run Ollama with `ollama serve` and pull the model: `ollama pull qwen2.5-vl:7b`.
   - **qwen2.5-vl-llamacpp**: Start a llama.cpp server on port 8090 with the Qwen2.5-VL model.

## Running the Benchmark

```bash
# Run with all available backends, 50 samples (default)
python scripts/benchmark_grounding.py

# Run specific backends
python scripts/benchmark_grounding.py --backends claude-sonnet qwen2.5-vl-ollama

# Fewer samples for a quick test
python scripts/benchmark_grounding.py --n 10

# Filter to macOS screenshots only
python scripts/benchmark_grounding.py --category macOS

# Force re-download of dataset
python scripts/benchmark_grounding.py --no-cache

# Save annotated screenshots
python scripts/benchmark_grounding.py --save-screenshots
```

## Interpreting Results

The benchmark prints a table like:

```
================================================================
GROUNDING BENCHMARK RESULTS
================================================================
Backend              | Accuracy | Avg Dist (px) | Avg Latency | Cost (est.)
---------------------+----------+---------------+-------------+------------
claude-sonnet        |   72.0%  |     45.3      |    1.23s    |   $0.2520
qwen2.5-vl-ollama    |   58.0%  |     78.1      |    3.45s    |   $0.0000
================================================================
```

- **Accuracy**: Fraction of predictions that land inside the ground-truth bounding box.
- **Avg Dist (px)**: Mean Euclidean distance in pixels from prediction to bbox center. Failed/timed-out predictions receive the full image diagonal as a penalty.
- **Avg Latency**: Mean inference time per sample.
- **Cost (est.)**: Estimated API cost based on token pricing. Local backends show $0.

The **winner** is the backend with the highest accuracy, with ties broken by lowest latency.

Results are also saved as JSON to `logs/benchmark_grounding_{timestamp}.json`.

## Adding a New Backend

1. Add an entry to the `BACKENDS` dict in `scripts/benchmark_grounding.py`:
   ```python
   BACKENDS["my-new-backend"] = {
       "type": "openai_compat",  # or "anthropic"
       "url": "http://localhost:8080/v1/chat/completions",
       "model": "my-model-name",
   }
   ```

2. If the model uses a new coordinate format, add it to `COORDINATE_SPACES`:
   ```python
   COORDINATE_SPACES["my-model"] = "normalized_0_1000"  # or "pixel", "normalized_0_1"
   ```

3. If it is a paid API, add pricing to the `PRICING` dict:
   ```python
   PRICING["my-new-backend"] = {"input": 1.50, "output": 7.50}
   ```

4. Run the benchmark with `--backends my-new-backend` to test it.
