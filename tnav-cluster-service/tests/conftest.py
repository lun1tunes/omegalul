"""Make the service ``app`` importable from any cwd; ``app/__init__.py`` then puts the repo kit folder on ``sys.path``."""

import sys
from pathlib import Path

_SERVICE = Path(__file__).resolve().parents[1]
if str(_SERVICE) not in sys.path:
    sys.path.insert(0, str(_SERVICE))

import app  # noqa: E402,F401
