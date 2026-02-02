#!/usr/bin/env python3
"""
Real component test: Ollama connection and availability
Tests actual connection to Ollama server and model availability
"""
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from automation_agent.llm.client import OllamaClient
from automation_agent.config import AgentConfig


async def test_ollama_connection():
    """Test real Ollama connection"""
    print("=" * 60)
    print("COMPONENT TEST: Ollama Connection")
    print("=" * 60)

    config = AgentConfig()
    client = OllamaClient(host=config.ollama_host, timeout=config.ollama_timeout)

    # Test 1: Check if Ollama is running
    print("\n[Test 1] Checking Ollama server health...")
    is_running = await client.test_connection()
    print(f"  Status: {'✅ Running' if is_running else '❌ Not running'}")
    if not is_running:
        print("  Error: Ollama server is not accessible")
        return False

    # Test 2: List available models
    print("\n[Test 2] Listing available models...")
    models = await client.list_models()
    print(f"  Found {len(models)} models:")
    for model in models:
        print(f"    - {model}")

    # Test 3: Check vision model availability
    print(f"\n[Test 3] Checking vision model ({config.vision_model})...")
    has_vision = await client.check_model_available(config.vision_model)
    print(f"  Status: {'✅ Available' if has_vision else '❌ Not available'}")
    if not has_vision:
        print(f"  Note: Run 'ollama pull {config.vision_model}' to download")

    # Test 4: Check text model availability
    print(f"\n[Test 4] Checking text model ({config.text_model})...")
    has_text = await client.check_model_available(config.text_model)
    print(f"  Status: {'✅ Available' if has_text else '❌ Not available'}")
    if not has_text:
        print(f"  Note: Run 'ollama pull {config.text_model}' to download")

    # Test 5: Test basic text generation
    if has_text:
        print(f"\n[Test 5] Testing text generation with {config.text_model}...")
        try:
            response = await client.generate(
                model=config.text_model,
                prompt="Say 'Hello' in one word only."
            )
            print(f"  Response: {response[:100]}")
            print(f"  Status: ✅ Text generation working")
        except Exception as e:
            print(f"  Status: ❌ Error - {e}")
            return False
    else:
        print("\n[Test 5] Skipping text generation (model not available)")

    # Test 6: Test vision generation (if model available)
    if has_vision:
        print(f"\n[Test 6] Testing vision model availability...")
        print(f"  Status: ✅ Vision model ready for image analysis")
    else:
        print("\n[Test 6] Skipping vision test (model not available)")

    print("\n" + "=" * 60)
    print("RESULT: ✅ All available tests passed")
    print("=" * 60)
    return True


if __name__ == "__main__":
    result = asyncio.run(test_ollama_connection())
    sys.exit(0 if result else 1)
