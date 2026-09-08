#!/usr/bin/env python3
"""Generate Agent — Schedule Builder from its spec (``agents/schedule_builder.py``)."""

from agents import SCHEDULE_BUILDER
from mas_agent_workflow import write_workflow

if __name__ == "__main__":
    write_workflow(SCHEDULE_BUILDER)
