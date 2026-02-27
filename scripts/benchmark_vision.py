#!/usr/bin/env python3
"""Benchmark vision inference: llama.cpp vs Ollama (same Qwen2.5-VL architecture).

Usage:
    # Start llama.cpp server first:
    #   llama-server -m ~/models/qwen2.5-vl-7b/Qwen2.5-VL-7B-Instruct-q4_k_m.gguf \
    #       --mmproj ~/models/qwen2.5-vl-7b/Qwen2.5-VL-7B-Instruct-mmproj-f16.gguf \
    #       --port 8090

    # Ensure Ollama is running with qwen3-vl:
    #   ollama serve  (if not already running)

    python3 scripts/benchmark_vision.py
"""

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

SCREENSHOT_PATH = "/tmp/benchmark_screenshot.png"
PROMPT = "Describe what you see on this screen in 2-3 sentences. What application is in the foreground?"

NUM_RUNS = 3

# Backend configs
BACKENDS = {
    "ollama": {
        "url": "http://localhost:11434/v1/chat/completions",
        "model": "qwen3-vl",
    },
    "llama.cpp": {
        "url": "http://localhost:8090/v1/chat/completions",
        "model": "Qwen2.5-VL-7B-Instruct",
    },
}


def take_screenshot():
    """Capture a fresh screenshot."""
    subprocess.run(["screencapture", "-x", SCREENSHOT_PATH], check=True)
    print(f"Screenshot saved: {SCREENSHOT_PATH}")


def load_screenshot_b64() -> str:
    data = Path(SCREENSHOT_PATH).read_bytes()
    return base64.b64encode(data).decode()


def call_vision_api(url: str, model: str, image_b64: str, prompt: str) -> dict:
    """Call OpenAI-compatible vision API and return response + timing."""
    import urllib.request

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 256,
        "stream": False,
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
        elapsed = time.perf_counter() - start
        content = result["choices"][0]["message"]["content"]
        tokens = result.get("usage", {})
        return {
            "success": True,
            "elapsed_s": round(elapsed, 2),
            "content": content,
            "prompt_tokens": tokens.get("prompt_tokens", "?"),
            "completion_tokens": tokens.get("completion_tokens", "?"),
        }
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "success": False,
            "elapsed_s": round(elapsed, 2),
            "error": str(e),
        }


def check_backend(name: str, url: str) -> bool:
    """Check if a backend is reachable."""
    import urllib.request

    health_url = url.rsplit("/v1/", 1)[0] + "/v1/models"
    try:
        req = urllib.request.Request(health_url)
        with urllib.request.urlopen(req, timeout=5):
            return True
    except Exception:
        # Try base health endpoint
        base_url = url.rsplit("/v1/", 1)[0]
        try:
            req = urllib.request.Request(base_url)
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            return False


def main():
    print("=" * 60)
    print("Vision Backend Benchmark: llama.cpp vs Ollama")
    print("=" * 60)
    print(f"Prompt: {PROMPT}")
    print(f"Runs per backend: {NUM_RUNS}")
    print()

    # Take fresh screenshot
    take_screenshot()
    image_b64 = load_screenshot_b64()
    print(f"Screenshot size: {len(image_b64) * 3 // 4 // 1024} KB")
    print()

    results = {}

    for name, cfg in BACKENDS.items():
        print(f"--- {name} ---")
        if not check_backend(name, cfg["url"]):
            print(f"  SKIP: {name} not reachable at {cfg['url']}")
            results[name] = {"available": False}
            print()
            continue

        results[name] = {"available": True, "runs": []}
        print(f"  URL: {cfg['url']}")
        print(f"  Model: {cfg['model']}")

        for i in range(NUM_RUNS):
            print(f"  Run {i+1}/{NUM_RUNS}... ", end="", flush=True)
            r = call_vision_api(cfg["url"], cfg["model"], image_b64, PROMPT)
            results[name]["runs"].append(r)

            if r["success"]:
                print(
                    f"{r['elapsed_s']}s "
                    f"(prompt_tok={r['prompt_tokens']}, "
                    f"completion_tok={r['completion_tokens']})"
                )
                if i == 0:
                    # Print first response
                    print(f"  Response: {r['content'][:200]}...")
            else:
                print(f"FAILED ({r['elapsed_s']}s): {r['error'][:100]}")
        print()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)

    for name, data in results.items():
        if not data.get("available"):
            print(f"{name:>12}: NOT AVAILABLE")
            continue

        times = [r["elapsed_s"] for r in data["runs"] if r["success"]]
        if not times:
            print(f"{name:>12}: ALL RUNS FAILED")
            continue

        avg = sum(times) / len(times)
        fastest = min(times)
        slowest = max(times)
        print(
            f"{name:>12}: avg={avg:.1f}s  "
            f"min={fastest:.1f}s  max={slowest:.1f}s  "
            f"({len(times)}/{NUM_RUNS} succeeded)"
        )

    # Winner
    avgs = {}
    for name, data in results.items():
        if data.get("available"):
            times = [r["elapsed_s"] for r in data["runs"] if r["success"]]
            if times:
                avgs[name] = sum(times) / len(times)
    if len(avgs) >= 2:
        winner = min(avgs, key=avgs.get)
        loser = max(avgs, key=avgs.get)
        speedup = avgs[loser] / avgs[winner]
        print(f"\nWinner: {winner} ({speedup:.1f}x faster than {loser})")


if __name__ == "__main__":
    main()
