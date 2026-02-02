"""Intent parser using LLM to convert natural language to structured actions."""

import json
import re
from typing import Optional

from ..llm.client import OllamaClient
from .models import Intent

# System prompt for intent parsing
INTENT_PARSER_SYSTEM_PROMPT = """You are a macOS automation intent parser. Convert user commands into JSON actions.

## Output Schema
Return ONLY valid JSON with this structure:
{
    "steps": [
        {"action": "ACTION_TYPE", "params": {...}}
    ],
    "requires_observation": true/false
}

## Available Actions

1. **activate_app** - Launch or bring an app to front
   params: {"app_name": "App Name"}
   Example: {"action": "activate_app", "params": {"app_name": "Safari"}}

2. **open_url** - Open URL in browser
   params: {"url": "https://...", "browser": "Safari"}
   Example: {"action": "open_url", "params": {"url": "https://youtube.com", "browser": "Safari"}}

3. **quit_app** - Close an application
   params: {"app_name": "App Name"}
   Example: {"action": "quit_app", "params": {"app_name": "Safari"}}

4. **type_text** - Type text at cursor position
   params: {"text": "text to type"}
   Example: {"action": "type_text", "params": {"text": "hello world"}}

5. **press_key** - Press keyboard shortcut
   params: {"keys": ["modifier", "key"]} or {"keys": ["key"]}
   Example: {"action": "press_key", "params": {"keys": ["command", "c"]}}
   Common keys: command, shift, option, control, return, tab, escape, space, delete

6. **click** - Click at screen coordinates (requires vision)
   params: {"x": number, "y": number}
   Example: {"action": "click", "params": {"x": 500, "y": 300}}

7. **click_element** - Click UI element by description (requires vision to find)
   params: {"description": "element description"}
   Example: {"action": "click_element", "params": {"description": "the search button"}}

## Rules

1. **URL Inference**: Convert partial names to full URLs
   - "youtube" → "https://www.youtube.com"
   - "google" → "https://www.google.com"
   - "github" → "https://github.com"

2. **Search Queries**: For "search X for Y" commands, use direct search URLs (NOT type_text):
   - "search YouTube for cats" → open_url with "https://www.youtube.com/results?search_query=cats"
   - "search Google for weather" → open_url with "https://www.google.com/search?q=weather"
   - "google something" → open_url with "https://www.google.com/search?q=something"
   This is MORE RELIABLE than typing in search boxes.

3. **App Name Normalization**: Use exact macOS app names
   - "chrome" → "Google Chrome"
   - "safari" → "Safari"
   - "terminal" → "Terminal"
   - "finder" → "Finder"
   - "vscode" or "code" → "Visual Studio Code"
   - "slack" → "Slack"
   - "spotify" → "Spotify"

3. **requires_observation**: Set to true ONLY when the task needs to:
   - Read or understand screen content
   - Find UI elements by visual description
   - Make decisions based on what's visible
   - Click on dynamically positioned elements
   - Verify if an action succeeded

4. **Simple vs Complex Tasks**:
   - Simple (requires_observation: false): "Open Safari", "Go to youtube.com", "Quit Chrome"
   - Complex (requires_observation: true): "Click the most popular video", "Find and click the login button", "Search for X and click the first result"

5. **Multi-step Commands**: Break complex commands into sequential steps
   - "Open Safari and go to google.com" → [activate_app Safari, open_url google.com]

## Examples

Input: "Open Safari"
Output: {"steps": [{"action": "activate_app", "params": {"app_name": "Safari"}}], "requires_observation": false}

Input: "Open Safari and go to youtube.com"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.youtube.com", "browser": "Safari"}}], "requires_observation": false}

Input: "Search YouTube for cooking tutorials"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.youtube.com/results?search_query=cooking+tutorials", "browser": "Safari"}}], "requires_observation": false}

Input: "Google the weather in San Francisco"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.google.com/search?q=weather+in+San+Francisco", "browser": "Safari"}}], "requires_observation": false}

Input: "Find the most viewed video and click on it"
Output: {"steps": [{"action": "click_element", "params": {"description": "the video with most views"}}], "requires_observation": true}

Input: "Close Chrome"
Output: {"steps": [{"action": "quit_app", "params": {"app_name": "Google Chrome"}}], "requires_observation": false}

Output ONLY valid JSON, no explanation or markdown."""


class IntentParser:
    """Parses natural language commands into structured intents using LLM."""

    def __init__(
        self,
        llm_client: OllamaClient,
        model: str = "gemma2:9b",
    ):
        """
        Initialize the intent parser.

        Args:
            llm_client: Ollama client for LLM inference
            model: Model name to use for parsing (default: gemma2:9b)
        """
        self.client = llm_client
        self.model = model

    async def parse(self, user_prompt: str) -> Intent:
        """
        Parse a user's natural language command into a structured Intent.

        Args:
            user_prompt: The user's command in natural language

        Returns:
            Intent object with parsed steps and metadata

        Raises:
            ValueError: If the LLM response cannot be parsed
        """
        response = await self.client.generate(
            model=self.model,
            prompt=f"Parse this command: {user_prompt}",
            system=INTENT_PARSER_SYSTEM_PROMPT,
            format="json",
        )

        # Parse the JSON response
        parsed_data = self._parse_json_response(response)

        return Intent.from_dict(parsed_data, raw_prompt=user_prompt)

    def _parse_json_response(self, response: str) -> dict:
        """
        Parse JSON from LLM response, handling common formatting issues.

        Args:
            response: Raw LLM response string

        Returns:
            Parsed dictionary

        Raises:
            ValueError: If JSON cannot be parsed
        """
        # Try direct parsing first
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown code blocks
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", response)
        if json_match:
            try:
                return json.loads(json_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try to find JSON object in the response
        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Could not parse JSON from LLM response: {response[:200]}...")

    async def classify_complexity(self, user_prompt: str) -> bool:
        """
        Quickly determine if a task requires observation (vision).

        Args:
            user_prompt: The user's command

        Returns:
            True if task requires observation, False for simple execution
        """
        # Keywords that indicate simple tasks (no vision needed)
        simple_keywords = [
            "open",
            "launch",
            "start",
            "quit",
            "close",
            "exit",
            "go to",
            "navigate to",
            "visit",
        ]

        # Keywords that indicate complex tasks (vision needed)
        complex_keywords = [
            "click",
            "find",
            "search for",
            "most popular",
            "first result",
            "best",
            "select",
            "choose",
            "pick",
            "look for",
            "locate",
            "identify",
        ]

        prompt_lower = user_prompt.lower()

        # Check for complex indicators
        for keyword in complex_keywords:
            if keyword in prompt_lower:
                return True

        # If only simple keywords, it's a simple task
        for keyword in simple_keywords:
            if keyword in prompt_lower:
                return False

        # Default to requiring observation for safety
        return True
