#!/usr/bin/env python3
"""Generate Agent — Excel Extractor from its spec (``agents/excel_extractor.py``)."""

from agents import EXCEL_EXTRACTOR
from mas_agent_workflow import write_workflow

if __name__ == "__main__":
    write_workflow(EXCEL_EXTRACTOR)
