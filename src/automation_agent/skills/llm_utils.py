"""Shared LLM dispatch for the skill subsystem."""

from __future__ import annotations

from automation_agent.config import AgentConfig


async def call_skill_llm(
    config: AgentConfig,
    prompt: str,
    max_tokens: int = 1024,
    model: str = "",
    temperature: float = 0.0,
) -> str:
    """Shared LLM dispatch for the skill subsystem.

    Routes to Anthropic or local OpenAI-compatible endpoint based on
    config.model_provider.
    """
    provider = getattr(config.model_provider, "value", config.model_provider)
    if provider == "anthropic":
        return await _call_anthropic(config, prompt, max_tokens, model, temperature)
    if provider == "gemini":
        return await _call_gemini(config, prompt, max_tokens, model, temperature)
    return await _call_local(config, prompt, max_tokens, model, temperature)


async def _call_local(
    config: AgentConfig,
    prompt: str,
    max_tokens: int,
    model: str,
    temperature: float,
) -> str:
    import httpx

    base = getattr(config, "text_server_url", None) or config.vision_server_url
    url = f"{base}/v1/chat/completions"
    payload = {
        "model": model or config.text_model or config.vision_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=config.vision_server_timeout) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


async def _call_gemini(
    config: AgentConfig,
    prompt: str,
    max_tokens: int,
    model: str,
    temperature: float,
) -> str:
    import asyncio

    from google import genai

    client = genai.Client(api_key=config.gemini_api_key)
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=model or config.gemini_model,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        ),
    )
    return response.text or ""


async def _call_anthropic(
    config: AgentConfig,
    prompt: str,
    max_tokens: int,
    model: str,
    temperature: float,
) -> str:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
    message = await client.messages.create(
        model=model or config.anthropic_model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text
