#!/usr/bin/env python3
"""
Test restaurant booking flow to verify:
1. Retina coordinate scaling fix (Bug #001)
2. Time-specific slot selection (Bug #002)
"""

import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Load .env file
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(env_path)

from automation_agent.config import AgentConfig, ModelProvider
from automation_agent.orchestrator.intent_parser import IntentParser
from automation_agent.orchestrator.observer import ScreenObserver
from automation_agent.orchestrator.action_registry import ActionRegistry
from automation_agent.orchestrator.agent import AutomationAgent
from automation_agent.perception.capture import ScreenCapturer


async def create_agent():
    """Create an agent configured for Anthropic."""
    config = AgentConfig()

    # Force Anthropic provider
    config.model_provider = ModelProvider.ANTHROPIC

    if not config.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in environment")
        sys.exit(1)

    print(f"Using provider: {config.model_provider.value}")
    print(f"Vision model: {config.anthropic_vision_model}")

    # Create appropriate client based on provider
    if config.model_provider == ModelProvider.ANTHROPIC:
        from automation_agent.llm.anthropic_client import AnthropicClient
        llm_client = AnthropicClient(api_key=config.anthropic_api_key)
        vision_client = llm_client
        text_model = config.anthropic_model
        vision_model = config.anthropic_vision_model
    else:
        from automation_agent.llm.client import OllamaClient
        llm_client = OllamaClient(host=config.ollama_host)
        vision_client = llm_client
        text_model = config.text_model
        vision_model = config.vision_model

    # Create components
    capturer = ScreenCapturer(config)
    parser = IntentParser(llm_client, model=text_model)
    observer = ScreenObserver(vision_client, capturer, model=vision_model)
    registry = ActionRegistry()

    # Create agent
    agent = AutomationAgent(
        parser=parser,
        observer=observer,
        registry=registry,
        llm_client=llm_client,
        text_model=text_model,
        max_iterations=25,
        action_delay=1.5,
    )

    return agent, capturer


async def save_screenshot(capturer, name):
    """Save a screenshot for debugging."""
    import base64
    from PIL import Image
    import io

    output_dir = "/tmp/agent_screenshots"
    os.makedirs(output_dir, exist_ok=True)

    screenshot = capturer.capture_screen()
    path = os.path.join(output_dir, f"{name}.png")
    screenshot.save(path)
    print(f"Screenshot saved: {path}")
    return path


async def main():
    print("=" * 60)
    print("RESTAURANT BOOKING TEST")
    print("Testing: Coordinate scaling + Time-specific selection")
    print("=" * 60)

    # Create agent
    agent, capturer = await create_agent()

    # Save initial state
    await save_screenshot(capturer, "booking_test_start")

    # The test prompt - specific restaurant, party size, and TIME
    test_prompt = "Book a table for 2 at Joey Valley Fair Restaurant for tonight at 7pm"

    print(f"\nTest prompt: {test_prompt}")
    print("-" * 60)

    # Execute
    print("\nStarting agent execution...")
    result = await agent.execute(test_prompt)

    # Save final state
    await save_screenshot(capturer, "booking_test_end")

    # Print results
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Success: {result.success}")
    print(f"Message: {result.message}")
    if hasattr(result, 'iterations'):
        print(f"Iterations: {result.iterations}")

    print("\nAction History:")
    for i, step in enumerate(result.steps, 1):
        status = "✓" if step.success else "✗"
        print(f"  {i}. [{status}] {step.action}: {step.params}")
        if step.error:
            print(f"      Error: {step.error}")
        # Show scaling info for clicks
        if step.action == "click" and "scaled_x" in str(step.params):
            print(f"      (Coordinate scaling applied)")

    print("\n" + "=" * 60)

    # Verify coordinate scaling was applied
    click_actions = [s for s in result.steps if s.action == "click"]
    if click_actions:
        print("\nCOORDINATE SCALING CHECK:")
        for click in click_actions:
            params = click.params
            if "scaled_x" in str(params) or "target" in str(params):
                print(f"  ✓ Click used scaling: {params}")
            else:
                print(f"  ? Click params: {params}")

    return result


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result.success else 1)
