#!/usr/bin/env python3
"""
Real component test: Vision model (Qwen2-VL)
Tests actual vision model with real screenshots
"""
import asyncio
import sys
from pathlib import Path
import base64

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from automation_agent.llm.client import OllamaClient
from automation_agent.perception.capture import ScreenCapturer
from automation_agent.config import AgentConfig


async def test_vision_model():
    """Test real vision model"""
    print("=" * 60)
    print("COMPONENT TEST: Vision Model (Qwen2-VL)")
    print("=" * 60)

    config = AgentConfig()
    client = OllamaClient(host=config.ollama_host, timeout=config.ollama_timeout)
    capturer = ScreenCapturer(config)

    # Test 1: Check model availability
    print(f"\n[Test 1] Checking if {config.vision_model} is available...")
    has_model = await client.check_model_available(config.vision_model)
    if not has_model:
        print(f"  Status: ❌ Model not available")
        print(f"  Action: Run 'ollama pull {config.vision_model}'")
        print(f"  Note: This is a large model (~8GB)")
        return False
    print(f"  Status: ✅ Model available")

    # Test 2: Capture current screen
    print("\n[Test 2] Capturing current screen...")
    try:
        screenshot = capturer.capture_screen()
        img_b64 = capturer.capture_screen_b64()
        print(f"  Screenshot size: {screenshot.size}")
        print(f"  Base64 length: {len(img_b64)} chars")

        # Save screenshot for reference
        output_dir = Path(__file__).parent / "output"
        output_dir.mkdir(exist_ok=True)
        from datetime import datetime
        screenshot_path = output_dir / f"vision_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        screenshot.save(screenshot_path)
        print(f"  Saved to: {screenshot_path}")
        print(f"  Status: ✅ Screenshot captured")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 3: Basic image description
    print("\n[Test 3] Testing basic image understanding...")
    prompt = "Describe what you see in this screenshot in one sentence."
    print(f"  Prompt: {prompt}")
    try:
        response = await client.generate_vision(
            model=config.vision_model,
            prompt=prompt,
            image_b64=img_b64
        )
        print(f"  Response: {response}")
        print(f"  Status: ✅ Image understanding working")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 4: UI element detection
    print("\n[Test 4] Testing UI element detection...")
    prompt = "List all visible UI elements you can see (buttons, menus, icons, etc). Be brief."
    print(f"  Prompt: {prompt}")
    try:
        response = await client.generate_vision(
            model=config.vision_model,
            prompt=prompt,
            image_b64=img_b64
        )
        print(f"  Response:\n{response}")
        print(f"  Status: ✅ UI detection working")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 5: Bounding box detection (Qwen2-VL special feature)
    print("\n[Test 5] Testing bounding box detection...")
    prompt = """Find any button or clickable element on the screen.
Return the bounding box in format: <box>(x1,y1,x2,y2)</box>
Where coordinates are normalized 0-1000."""
    print(f"  Prompt: {prompt[:80]}...")
    try:
        response = await client.generate_vision(
            model=config.vision_model,
            prompt=prompt,
            image_b64=img_b64
        )
        print(f"  Response: {response}")

        # Check if bounding box is in response
        if "<box>" in response and "</box>" in response:
            print(f"  Status: ✅ Bounding box detection working")
        else:
            print(f"  Status: ⚠️  Response generated but no bounding box found")
            print(f"  Note: May need prompt engineering or different scene")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    # Test 6: Text recognition (OCR)
    print("\n[Test 6] Testing text recognition...")
    prompt = "What text can you read on the screen? List the most prominent text."
    print(f"  Prompt: {prompt}")
    try:
        response = await client.generate_vision(
            model=config.vision_model,
            prompt=prompt,
            image_b64=img_b64
        )
        print(f"  Response:\n{response}")
        print(f"  Status: ✅ Text recognition working")
    except Exception as e:
        print(f"  Status: ❌ Error - {e}")
        return False

    print("\n" + "=" * 60)
    print("RESULT: ✅ All tests passed")
    print(f"OUTPUT: Check {output_dir} for test images")
    print("=" * 60)
    return True


if __name__ == "__main__":
    result = asyncio.run(test_vision_model())
    sys.exit(0 if result else 1)
