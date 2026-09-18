#!/usr/bin/env python3
"""Generate Agent — tNav Cluster Agent from its spec (``agents/tnav_cluster.py``).

Workflow лежит в основном каталоге (``n8n/workflows/core``): агент боевой, просто выключен в реестре,
пока инженер не прописал доступ к кластеру и не включил его в Activity → Агенты.
"""

from agents import TNAV_CLUSTER
from mas_agent_workflow import write_workflow

if __name__ == "__main__":
    write_workflow(TNAV_CLUSTER)
