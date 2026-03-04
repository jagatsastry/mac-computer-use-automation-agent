#!/usr/bin/env python3
"""Minimal OpenAI-compatible server for mlx-vlm models.

Provides /v1/models and /v1/chat/completions endpoints.
Used as a workaround when mlx_vlm.server is not available (requires Python 3.10+).

Usage:
    python scripts/mlx_vlm_server.py --model mlx-community/Molmo-7B-D-0924-4bit --port 8091
"""

import argparse
import base64
import io
import json
import tempfile
import time
import uuid

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

# Global state set after model loads
_model = None
_processor = None
_config = None
_model_name = ""


def _load_model(model_path: str):
    global _model, _processor, _config, _model_name
    from mlx_vlm import load
    print(f"Loading model: {model_path} ...")
    _model, _processor = load(model_path, trust_remote_code=True)
    _config = getattr(_model, "config", None)
    _model_name = model_path
    print(f"Model loaded: {model_path}")


async def list_models(request: Request) -> JSONResponse:
    return JSONResponse({
        "object": "list",
        "data": [
            {
                "id": _model_name,
                "object": "model",
                "owned_by": "local",
            }
        ],
    })


async def chat_completions(request: Request) -> JSONResponse:
    from mlx_vlm import generate
    from PIL import Image

    body = await request.json()
    messages = body.get("messages", [])
    max_tokens = body.get("max_tokens", 256)

    # Extract text and image from messages
    prompt_text = ""
    image_path = None
    tmp_file = None

    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            prompt_text += content
        elif isinstance(content, list):
            for part in content:
                if part.get("type") == "text":
                    prompt_text += part.get("text", "")
                elif part.get("type") == "image_url":
                    image_url = part.get("image_url", {}).get("url", "")
                    if image_url.startswith("data:"):
                        # data:image/...;base64,...
                        _, b64data = image_url.split(",", 1)
                        image_bytes = base64.b64decode(b64data)
                        image = Image.open(io.BytesIO(image_bytes))
                        # Resize large images to prevent Metal GPU OOM
                        max_dim = 768
                        if max(image.size) > max_dim:
                            ratio = max_dim / max(image.size)
                            new_size = (int(image.width * ratio), int(image.height * ratio))
                            image = image.resize(new_size, Image.LANCZOS)
                        # mlx-vlm 0.3.x generate() expects file paths, not PIL images
                        tmp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                        image.save(tmp_file, format="PNG")
                        tmp_file.close()
                        image_path = tmp_file.name

    # Apply chat template so the processor inserts image tokens (e.g. <|image|>)
    from mlx_vlm.prompt_utils import apply_chat_template
    formatted_prompt = apply_chat_template(
        _processor,
        config=_config,
        prompt=prompt_text,
        num_images=1 if image_path is not None else 0,
    )

    start = time.perf_counter()
    try:
        if image_path is not None:
            output = generate(
                _model,
                _processor,
                formatted_prompt,
                image_path,
                max_tokens=max_tokens,
                verbose=False,
            )
        else:
            output = generate(
                _model,
                _processor,
                formatted_prompt,
                max_tokens=max_tokens,
                verbose=False,
            )
    finally:
        if tmp_file is not None:
            import os
            os.unlink(tmp_file.name)
    elapsed = time.perf_counter() - start

    return JSONResponse({
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": _model_name,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": output.text if hasattr(output, "text") else (output if isinstance(output, str) else str(output)),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    })


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


app = Starlette(
    routes=[
        Route("/v1/models", list_models, methods=["GET"]),
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/health", health, methods=["GET"]),
        Route("/", health, methods=["GET"]),
    ]
)


def main():
    parser = argparse.ArgumentParser(description="Minimal OpenAI-compatible mlx-vlm server")
    parser.add_argument("--model", required=True, help="HuggingFace model ID")
    parser.add_argument("--port", type=int, default=8091, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    args = parser.parse_args()

    _load_model(args.model)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
