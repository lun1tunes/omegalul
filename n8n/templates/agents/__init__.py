"""Agent specs — one module per agent; ``ALL`` feeds the registry seed, Runtime Config and the generators.

Add an agent: create ``agents/<agent_id>.py`` with ``SPEC = AgentSpec(...)`` and append it here.
"""

from .calculation_agent import SPEC as CALCULATION_AGENT
from .demo_agent import SPEC as DEMO_AGENT
from .excel_extractor import SPEC as EXCEL_EXTRACTOR
from .schedule_builder import SPEC as SCHEDULE_BUILDER
from .tnav_cluster import SPEC as TNAV_CLUSTER

# DEMO_AGENT is the template agent (agents-template/demo_agent): seeded ``enabled=False``, lives in workflows/support.
# TNAV_CLUSTER (hydrodynamic cluster over SSH) is seeded ``enabled=False`` too: the engineer switches it on
# in Activity → Агенты once ``tnav-cluster.env`` has the cluster credentials.
ALL = (EXCEL_EXTRACTOR, SCHEDULE_BUILDER, CALCULATION_AGENT, TNAV_CLUSTER, DEMO_AGENT)

__all__ = ["ALL", "CALCULATION_AGENT", "DEMO_AGENT", "EXCEL_EXTRACTOR", "SCHEDULE_BUILDER", "TNAV_CLUSTER"]
