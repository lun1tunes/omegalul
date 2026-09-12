"""Read the repo ``VERSION`` file (field checkout or lab ``/app/VERSION`` mount)."""

from __future__ import annotations

from pathlib import Path

_EXTRA = (Path("/app"), Path("/mas-agent-kit"))


def read_mas_version() -> str:
    seen: set[Path] = set()
    for seed in (Path(__file__).resolve(), *_EXTRA):
        folder = seed if seed.is_dir() else seed.parent
        for candidate in (folder, *folder.parents):
            path = candidate / "VERSION"
            if path in seen:
                continue
            seen.add(path)
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                text = line.strip()
                if text and not text.startswith("#"):
                    return text
    return ""
