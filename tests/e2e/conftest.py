import pytest
from pathlib import Path
from automation_agent.config import AgentConfig
from automation_agent.planner import ActionPlannerImpl
from automation_agent.skills import SkillRegistryImpl
from automation_agent.vision import ScreenCoordinatorImpl
from automation_agent.actuator import HammerspoonActuator
from automation_agent.orchestrator import AutomationAgent
from automation_agent.logging.event_logger import EventLogger

@pytest.fixture
def real_config():
    """Real config from environment."""
    return AgentConfig()

@pytest.fixture
def real_actuator(real_config):
    return HammerspoonActuator(real_config)

@pytest.fixture
def real_agent(real_config, tmp_path):
    """Build a real agent with all components."""
    logger = EventLogger(tmp_path / "e2e_logs")
    planner = ActionPlannerImpl(real_config)
    skills = SkillRegistryImpl()
    coordinator = ScreenCoordinatorImpl(real_config)
    actuator = HammerspoonActuator(real_config)
    return AutomationAgent(
        planner=planner,
        skill_registry=skills,
        coordinator=coordinator,
        actuator=actuator,
        config=real_config,
        logger=logger,
    )

def requires_hammerspoon():
    """Skip test if Hammerspoon not available."""
    import shutil
    return pytest.mark.skipif(
        shutil.which("hs") is None,
        reason="Hammerspoon 'hs' CLI not available"
    )

def requires_anthropic():
    """Skip test if Anthropic API key not set."""
    import os
    return pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set"
    )
