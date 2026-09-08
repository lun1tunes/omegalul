#!/usr/bin/env python3
"""Generate Agent — Demo Agent from its spec (``agents/demo_agent.py``).

The template agent is optional: it lands in ``n8n/workflows/support`` (imported, but not part of the
runtime order) and its registry row is ``enabled=false`` until an engineer switches it on.
"""

from agents import DEMO_AGENT
from mas_agent_workflow import OUT_DIR, write_workflow

SUPPORT_DIR = OUT_DIR.parent / "support"

if __name__ == "__main__":
    write_workflow(DEMO_AGENT, out_dir=SUPPORT_DIR)
