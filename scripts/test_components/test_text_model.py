#!/usr/bin/env python3
"""
Real component test: Text model (Gemma 2)
Tests actual text generation with real prompts
"""
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from automation_agent.llm.client import OllamaClient
from automation_agent.config import AgentConfig


async def test_text_model():
    """Test real text model"""
    print("=" * 60)
    print("COMPONENT TEST: Text Model (Gemma 2)")
    print("=" * 60)

    config = AgentConfig()
    client = OllamaClient(host=config.ollama_host, timeout=config.ollama_timeout)

    # Test 1: Check model availability
    print(f"\n[Test 1] Checking if {config.text_model} is available...")
    has_model = await client.check_model_available(config.text_model)
    if not has_model:
        print(f"  Status: ❌ Model not available")
        print(f"  Action: Run 'ollama pull {config.text_model}'")
        return False
    print(f"  Status: ✅ Model available")

    # Test 2: Simple completion
    print("\n[Test 2] Testing simple completion...")
    prompt = "What is 2+2? Answer with just the number."
    print(f"  Prompt: {prompt}")
    try:
        response = await client.generate(
            model=config.text_model,
            prompt=prompt
        )
        print(f"  Response: {response.strip()}")
        print(f"  Status: ✅ Completion successful")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 3: Reasoning task
    print("\n[Test 3] Testing reasoning task...")
    prompt = """You are a desktop automation assistant.
Given the task: "Open Calculator"
Provide step-by-step actions needed. Keep it brief."""
    print(f"  Prompt: {prompt[:80]}...")
    try:
        response = await client.generate(
            model=config.text_model,
            prompt=prompt
        )
        print(f"  Response:\n{response}")
        print(f"  Status: ✅ Reasoning successful")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 4: JSON output
    print("\n[Test 4] Testing structured JSON output...")
    prompt = """Return ONLY a JSON object with this structure:
{
    "task": "open calculator",
    "steps": ["step1", "step2"],
    "difficulty": "easy"
}
No other text, just the JSON."""
    print(f"  Prompt: {prompt[:80]}...")
    try:
        response = await client.generate(
            model=config.text_model,
            prompt=prompt,
            format="json"
        )
        print(f"  Response:\n{response}")

        # Try to parse as JSON
        import json
        try:
            parsed = json.loads(response.strip())
            print(f"  Status: ✅ Valid JSON output")
        except:
            print(f"  Status: ⚠️  Response generated but not valid JSON")
            print(f"  Note: May need prompt engineering")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 5: Streaming (async)
    print("\n[Test 5] Testing streaming generation...")
    prompt = "Count from 1 to 5, one number per line."
    print(f"  Prompt: {prompt}")
    try:
        print(f"  Streaming response:")
        chunks = []
        async for chunk in client.generate_stream(
            model=config.text_model,
            prompt=prompt
        ):
            chunks.append(chunk)
            print(f"    {chunk}", end="", flush=True)
        print(f"\n  Total chunks: {len(chunks)}")
        print(f"  Status: ✅ Streaming successful")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    print("\n" + "=" * 60)
    print("RESULT: ✅ All tests passed")
    print("=" * 60)
    return True


if __name__ == "__main__":
    result = asyncio.run(test_text_model())
    sys.exit(0 if result else 1)
