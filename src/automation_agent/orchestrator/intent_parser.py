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

4. **requires_observation**: Set to true when the task needs to:
   - Read or understand screen content
   - Find UI elements by visual description
   - Make decisions based on what's visible
   - Click on dynamically positioned elements
   - Verify if an action succeeded
   - Fill in forms with multiple fields
   - Compare options and make choices (like finding cheapest/best)

5. **Simple vs Complex Tasks**:
   - Simple (requires_observation: false): "Open Safari", "Go to youtube.com", "Quit Chrome", "Search YouTube for cats"
   - Complex (requires_observation: true): "Click the most popular video", "Find and click the login button", "Find the cheapest flight", "Book a hotel", "Fill out a form"

6. **Multi-step Commands**: Break complex commands into sequential steps
   - "Open Safari and go to google.com" → [activate_app Safari, open_url google.com]

## Travel & Booking Search Patterns

Use direct URLs with search parameters pre-filled. This is MORE RELIABLE than navigating manually.

7. **Flight Searches** - Use Google Flights:
   - "find flights from X to Y" → "https://www.google.com/travel/flights?q=flights+from+X+to+Y"
   - Include dates in query: "flights+from+SFO+to+Tokyo+March+15-20+2026"
   - Set requires_observation: true to analyze results

8. **Hotel Searches** - Use Google Hotels:
   - "find hotels in CITY" → "https://www.google.com/travel/hotels/CITY"
   - "hotels in Tokyo March 15-20" → "https://www.google.com/travel/hotels/Tokyo?q=hotels+in+Tokyo+March+15+to+March+20+2026"
   - For specific dates, add: "&dates=2026-03-15,2026-03-20" (format: YYYY-MM-DD)
   - Set requires_observation: true to compare prices

9. **Restaurant Searches** - Use Google Maps:
   - "find restaurants near X" → "https://www.google.com/maps/search/restaurants+near+X"
   - "best sushi in Tokyo" → "https://www.google.com/maps/search/best+sushi+in+Tokyo"
   - "coffee shops nearby" → "https://www.google.com/maps/search/coffee+shops"
   - Set requires_observation: true for choosing/clicking

10. **Shopping Searches** - Use Google Shopping:
    - "find X for sale" → "https://www.google.com/search?q=X&tbm=shop"
    - "buy iPhone 15" → "https://www.google.com/search?q=buy+iPhone+15&tbm=shop"
    - "compare prices for X" → "https://www.google.com/search?q=X&tbm=shop"
    - Set requires_observation: true to compare/select

11. **Directions/Maps** - Use Google Maps:
    - "directions from X to Y" → "https://www.google.com/maps/dir/X/Y"
    - "how to get to X" → "https://www.google.com/maps/search/X"
    - Set requires_observation: false for simple directions display

12. **News Searches** - Use Google News:
    - "news about X" → "https://www.google.com/search?q=X&tbm=nws"
    - "latest news on Y" → "https://www.google.com/search?q=Y&tbm=nws"
    - Set requires_observation: true if selecting articles

13. **Image Searches** - Use Google Images:
    - "images of X" → "https://www.google.com/search?q=X&tbm=isch"
    - "pictures of cats" → "https://www.google.com/search?q=cats&tbm=isch"
    - Set requires_observation: true if selecting images

14. **Restaurant Reservations** - Use OpenTable for direct booking:
    - "book a table at RESTAURANT" → "https://www.opentable.com/s?term=RESTAURANT"
    - "reserve a table at RESTAURANT for N people" → "https://www.opentable.com/s?term=RESTAURANT&covers=N"
    - "make a reservation at RESTAURANT" → "https://www.opentable.com/s?term=RESTAURANT"
    - Include date/time in search if provided
    - Set requires_observation: true (need to select time slot and complete booking)

15. **Movie Tickets** - Use Fandango:
    - "buy tickets for MOVIE" → "https://www.fandango.com/search?q=MOVIE"
    - "movie showtimes for MOVIE" → "https://www.fandango.com/search?q=MOVIE"
    - Set requires_observation: true

16. **Event Tickets** - Use Ticketmaster:
    - "buy tickets for EVENT" → "https://www.ticketmaster.com/search?q=EVENT"
    - "concert tickets for ARTIST" → "https://www.ticketmaster.com/search?q=ARTIST"
    - Set requires_observation: true

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

Input: "Find the cheapest flight from San Francisco to Singapore in March 2026"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.google.com/travel/flights?q=flights+from+San+Francisco+to+Singapore+March+2026", "browser": "Safari"}}], "requires_observation": true}

Input: "Find flights from NYC to London next week"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.google.com/travel/flights?q=flights+from+NYC+to+London+next+week", "browser": "Safari"}}], "requires_observation": true}

Input: "Find me a hotel in Tokyo for March 15-20 2026"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.google.com/travel/hotels/Tokyo?q=hotels+in+Tokyo+March+15+to+20+2026&dates=2026-03-15,2026-03-20", "browser": "Safari"}}], "requires_observation": true}

Input: "Find hotels in Paris for next weekend"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.google.com/travel/hotels/Paris?q=hotels+in+Paris+next+weekend", "browser": "Safari"}}], "requires_observation": true}

Input: "Find the best restaurants near Times Square"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.google.com/maps/search/best+restaurants+near+Times+Square", "browser": "Safari"}}], "requires_observation": true}

Input: "Find sushi restaurants in San Francisco"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.google.com/maps/search/sushi+restaurants+in+San+Francisco", "browser": "Safari"}}], "requires_observation": true}

Input: "Compare prices for MacBook Pro"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.google.com/search?q=MacBook+Pro&tbm=shop", "browser": "Safari"}}], "requires_observation": true}

Input: "Get directions from San Francisco to Los Angeles"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.google.com/maps/dir/San+Francisco/Los+Angeles", "browser": "Safari"}}], "requires_observation": false}

Input: "Show me news about AI"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.google.com/search?q=AI&tbm=nws", "browser": "Safari"}}], "requires_observation": false}

Input: "Find images of golden gate bridge"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.google.com/search?q=golden+gate+bridge&tbm=isch", "browser": "Safari"}}], "requires_observation": false}

Input: "Book a table for two at Joey Valley Fair Restaurant for tonight at 7pm"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.opentable.com/s?term=Joey+Valley+Fair&covers=2&dateTime=2026-02-03T19:00", "browser": "Safari"}}], "requires_observation": true}

Input: "Make a reservation at Nobu for 4 people"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.opentable.com/s?term=Nobu&covers=4", "browser": "Safari"}}], "requires_observation": true}

Input: "Reserve a table at The French Laundry"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.opentable.com/s?term=The+French+Laundry", "browser": "Safari"}}], "requires_observation": true}

Input: "Buy movie tickets for Dune 2"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.fandango.com/search?q=Dune+2", "browser": "Safari"}}], "requires_observation": true}

Input: "Get tickets for Taylor Swift concert"
Output: {"steps": [{"action": "open_url", "params": {"url": "https://www.ticketmaster.com/search?q=Taylor+Swift", "browser": "Safari"}}], "requires_observation": true}

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
