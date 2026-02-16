"""Restaurant-focused interactive workflow with persistent user memory."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..orchestrator.agent import AutomationAgent
from ..orchestrator.models import ExecutionResult


MEMORY_FILE = "MEMORY.md"


@dataclass
class RestaurantRequest:
    """Structured reservation details collected from user input."""

    cuisine: str
    location: str
    date_time: str
    party_size: str


@dataclass
class ProviderOption:
    """Observed options from one provider."""

    provider: str
    query_url: str
    summary: str


def _extract_restaurant_fields(prompt: str) -> Dict[str, str]:
    """Best-effort extraction of reservation details from a user prompt."""
    lowered = prompt.lower()
    fields: Dict[str, str] = {}

    party_match = re.search(r"\bfor\s+(\d+)\b", lowered)
    if party_match:
        fields["party_size"] = party_match.group(1)

    cuisine_match = re.search(
        r"\b(sushi|thai|indian|italian|mexican|chinese|japanese|korean|mediterranean|vegan|steak)\b",
        lowered,
    )
    if cuisine_match:
        fields["cuisine"] = cuisine_match.group(1)

    location_match = re.search(r"\b(?:in|near)\s+([a-zA-Z\s]+?)(?:\s+for|\s+at|\s+on|\s*$)", prompt)
    if location_match:
        fields["location"] = location_match.group(1).strip()

    datetime_match = re.search(
        r"\b(tonight|tomorrow|next\s+\w+|[a-zA-Z]+\s+\d{1,2}(?:,\s*\d{4})?(?:\s+at\s+[0-9:apm\s]+)?)\b",
        prompt,
        flags=re.IGNORECASE,
    )
    if datetime_match:
        fields["date_time"] = datetime_match.group(1).strip()

    return fields


def _load_memory(memory_path: Path) -> Dict[str, str]:
    """Load last known user preferences from MEMORY.md."""
    if not memory_path.exists():
        return {}

    content = memory_path.read_text(encoding="utf-8")
    memory: Dict[str, str] = {}
    for key in ("Cuisine", "Location", "DateTime", "PartySize"):
        match = re.search(rf"- \*\*{key}\*\*: (.+)", content)
        if match:
            memory[key.lower()] = match.group(1).strip()
    return memory


def _upsert_memory(memory_path: Path, request: RestaurantRequest, selected: str) -> None:
    """Create or update MEMORY.md with latest preferences and decision."""
    timestamp = datetime.now().isoformat(timespec="seconds")

    if not memory_path.exists():
        memory_path.write_text(
            "# User Memory\n\n"
            "Persistent preferences used by the automation agent.\n\n"
            "## Latest Preferences\n"
            f"- **Cuisine**: {request.cuisine}\n"
            f"- **Location**: {request.location}\n"
            f"- **DateTime**: {request.date_time}\n"
            f"- **PartySize**: {request.party_size}\n\n"
            "## Decision History\n"
            f"- {timestamp}: Selected provider/option -> {selected}\n",
            encoding="utf-8",
        )
        return

    content = memory_path.read_text(encoding="utf-8")
    replacements = {
        "Cuisine": request.cuisine,
        "Location": request.location,
        "DateTime": request.date_time,
        "PartySize": request.party_size,
    }
    for key, value in replacements.items():
        pattern = rf"(- \*\*{key}\*\*: ).+"
        if re.search(pattern, content):
            content = re.sub(pattern, rf"\1{value}", content)
        else:
            if "## Latest Preferences" not in content:
                content += "\n## Latest Preferences\n"
            content += f"- **{key}**: {value}\n"

    if "## Decision History" not in content:
        content += "\n## Decision History\n"
    content += f"- {timestamp}: Selected provider/option -> {selected}\n"
    memory_path.write_text(content, encoding="utf-8")


async def _ask_user(question: str, default: Optional[str] = None) -> str:
    """Prompt user for input without blocking event loop."""
    suffix = f" [{default}]" if default else ""
    value = await asyncio.to_thread(input, f"{question}{suffix}: ")
    trimmed = value.strip()
    if trimmed:
        return trimmed
    return default or ""


async def _collect_request(prompt: str, memory_path: Path) -> RestaurantRequest:
    """Collect missing reservation fields interactively."""
    extracted = _extract_restaurant_fields(prompt)
    memory = _load_memory(memory_path)

    cuisine = extracted.get("cuisine") or await _ask_user(
        "Preferred cuisine/type", memory.get("cuisine")
    )
    location = extracted.get("location") or await _ask_user(
        "Preferred location/city/area", memory.get("location")
    )
    date_time = extracted.get("date_time") or await _ask_user(
        "Date/time (for example: tonight at 7pm)", memory.get("datetime")
    )
    party_size = extracted.get("party_size") or await _ask_user(
        "Party size", memory.get("partysize", "2")
    )

    return RestaurantRequest(
        cuisine=cuisine or "restaurant",
        location=location or "near me",
        date_time=date_time or "tonight",
        party_size=party_size or "2",
    )


def _provider_urls(req: RestaurantRequest) -> Dict[str, str]:
    """Build provider URLs for aggregated option collection."""
    q = f"{req.cuisine} restaurant in {req.location} for {req.party_size} {req.date_time}"
    opentable_q = q.replace(" ", "+")
    yelp_q = q.replace(" ", "+")
    google_q = q.replace(" ", "+")
    return {
        "OpenTable": f"https://www.opentable.com/s?term={opentable_q}",
        "Yelp": f"https://www.yelp.com/search?find_desc={yelp_q}",
        "Google": f"https://www.google.com/search?q={google_q}",
    }


async def _aggregate_options(agent: AutomationAgent, req: RestaurantRequest) -> List[ProviderOption]:
    """Gather top options from providers by navigating and observing with vision."""
    options: List[ProviderOption] = []
    for provider, url in _provider_urls(req).items():
        preload_goal = f"Open Safari and go to {url}"
        await agent.execute(preload_goal)
        await asyncio.sleep(max(agent.action_delay, 1.0))

        observation = await agent.observer.observe(
            f"From this {provider} page, list the top 3 restaurant options for "
            f"{req.cuisine} in {req.location} around {req.date_time}. "
            "Include names, rating/price if visible, and which one seems best."
        )
        options.append(ProviderOption(provider=provider, query_url=url, summary=observation.description))
    return options


async def run_restaurant_workflow(
    prompt: str,
    agent: AutomationAgent,
    repo_root: Path,
) -> ExecutionResult:
    """Run focused restaurant flow with user interaction and memory updates."""
    memory_path = repo_root / MEMORY_FILE
    request = await _collect_request(prompt, memory_path)

    print("\n[INFO] Gathering options across OpenTable, Yelp, and Google...")
    options = await _aggregate_options(agent, request)

    print("\n[OPTIONS] Aggregated restaurant suggestions:")
    for idx, option in enumerate(options, 1):
        print(f"\n{idx}. {option.provider} ({option.query_url})")
        print(option.summary)

    choice = await _ask_user(
        "Choose provider/option number to continue with reservation automation",
        "1",
    )
    try:
        selected_idx = max(1, min(len(options), int(choice))) - 1
    except ValueError:
        selected_idx = 0
    selected = options[selected_idx]

    _upsert_memory(
        memory_path,
        request,
        f"{selected.provider} -> {selected.query_url}",
    )

    final_goal = (
        f"Use {selected.provider} to reserve a {request.cuisine} restaurant in {request.location} "
        f"for {request.party_size} people at {request.date_time}. "
        "Proceed until reservation confirmation details are visible. "
        "If login is required, pause and wait for user to complete login."
    )
    print(f"\n[INFO] Starting reservation automation on {selected.provider}...")
    return await agent.execute(final_goal)
