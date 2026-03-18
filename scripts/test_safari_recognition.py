#!/usr/bin/env python3
"""
Test what vision models actually see when shown Safari icon directly
"""
import asyncio
import sys
from pathlib import Path
from PIL import Image
import io, base64

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation_agent.llm.client import OllamaClient
from automation_agent.perception.capture import ScreenCapturer
from automation_agent.config import AgentConfig

async def test_safari_recognition():
    """Show vision models Safari icon and ask what they see."""
    config = AgentConfig()
    client = OllamaClient(host=config.ollama_host, timeout=config.ollama_timeout)
    capturer = ScreenCapturer(config)

    print("=" * 70)
    print("VISION MODEL SAFARI RECOGNITION TEST")
    print("=" * 70)

    # Capture screenshot
    screenshot = capturer.capture_screen()
    img_width, img_height = screenshot.size

    # Extract Safari icon region (from debug findings: 241,952,267,999)
    # Add padding for context
    safari_crop = screenshot.crop((231, 942, 277, 1009))

    # Save it
    output_dir = Path(__file__).parent / "test_components" / "output"
    safari_icon_path = output_dir / "safari_icon_extracted.png"
    safari_crop.save(safari_icon_path)
    print(f"\n[1] Extracted Safari icon: {safari_icon_path.name}")
    print(f"    Size: {safari_crop.size}")

    # Convert to base64
    buffered = io.BytesIO()
    safari_crop.save(buffered, format="PNG")
    safari_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

    # Test with both vision models
    for model in ["qwen3-vl", "llava"]:
        print(f"\n{'=' * 70}")
        print(f"Testing with {model.upper()}")
        print(f"{'=' * 70}")

        prompts = [
            "What application icon is this? Answer with just the application name.",
            "Describe this icon in detail. What colors, shapes, and symbols do you see?",
            "Is this the Safari browser icon? Answer yes or no, then explain what you see.",
        ]

        for i, prompt in enumerate(prompts, 1):
            print(f"\n[Q{i}] {prompt}")
            try:
                response = await client.generate_vision(
                    model=model,
                    prompt=prompt,
                    image_b64=safari_b64
                )
                print(f"[A{i}] {response.strip()}")
            except Exception as e:
                print(f"[A{i}] ERROR: {e}")

    print(f"\n{'=' * 70}")
    print("TEST COMPLETE")
    print(f"{'=' * 70}")

if __name__ == "__main__":
    asyncio.run(test_safari_recognition())
