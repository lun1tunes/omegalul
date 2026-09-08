"""Excel Tools service.

The shared agent core ``mas_agent_kit`` is a plain folder of this repository (``<repo>/mas-agent-kit``),
not a pip package. Python only looks next to the entry point, so we add that folder to ``sys.path`` here,
before the first ``from mas_agent_kit import ...`` in ``app.main`` / ``app.agent``. Works the same for
``uvicorn app.main:app``, pytest and the lab container (the kit is mounted at ``/mas-agent-kit``).
"""

import sys
from pathlib import Path


def _use_repo_kit() -> None:
    for parent in Path(__file__).resolve().parents:
        kit = parent / "mas-agent-kit"
        if (kit / "mas_agent_kit" / "__init__.py").is_file():
            if str(kit) not in sys.path:
                sys.path.insert(0, str(kit))
            return


_use_repo_kit()
