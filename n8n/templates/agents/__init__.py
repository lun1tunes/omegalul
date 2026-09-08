"""Agent specs — one module per agent; ``ALL`` feeds the registry seed, Runtime Config and the generators.

Add an agent: create ``agents/<agent_id>.py`` with ``SPEC = AgentSpec(...)`` and append it here.
"""

from .calculation_agent import SPEC as CALCULATION_AGENT
from .demo_agent import SPEC as DEMO_AGENT
from .excel_extractor import SPEC as EXCEL_EXTRACTOR
from .schedule_builder import SPEC as SCHEDULE_BUILDER

# DEMO_AGENT is the template agent (agents-template/demo_agent): seeded ``enabled=False``, lives in workflows/support.
ALL = (EXCEL_EXTRACTOR, SCHEDULE_BUILDER, CALCULATION_AGENT, DEMO_AGENT)

__all__ = ["ALL", "CALCULATION_AGENT", "DEMO_AGENT", "EXCEL_EXTRACTOR", "SCHEDULE_BUILDER"]
