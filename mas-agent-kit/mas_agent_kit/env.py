"""Field ``*.env`` loader — the same contract as MAS Activity.

Windows CMD ``for /f`` corrupts a Notepad UTF-8 BOM (first key becomes ``\\ufeffNAME``) and splits
values on ``=`` (passwords, CLI commands). The field ``start-windows.bat`` must not parse the file:
Python reads it with ``python-dotenv`` and ``encoding=utf-8-sig``. Variables already set in the
process win (``override=False``).
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_LOADED: list[Path] = []


def load_service_env(service_root: str | Path, *filenames: str) -> list[Path]:
    """Load ``<service>/<name>``, then ``<service>/.env``, then repo ``.env``. First file wins per key."""
    root = Path(service_root).resolve()
    candidates = [root / name for name in filenames if name]
    candidates.append(root / ".env")
    repo = root.parent
    if (repo / "VERSION").is_file() or (repo / "mas-agent-kit").is_dir():
        candidates.append(repo / ".env")
    found: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        load_dotenv(resolved, override=False, encoding="utf-8-sig")
        found.append(resolved)
        if resolved not in _LOADED:
            _LOADED.append(resolved)
    return found


def loaded_env_files() -> list[str]:
    """Absolute paths already loaded in this process — for ``GET /health``."""
    return [str(path) for path in _LOADED]


def reset_loaded_env_files() -> None:
    """Tests only: drop the process-wide list so ``/health`` stays isolated."""
    _LOADED.clear()
