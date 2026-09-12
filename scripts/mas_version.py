"""One-line MAS version from the repo ``VERSION`` file. Stdlib only."""

from __future__ import annotations

from pathlib import Path

_EXTRA = (Path("/app"), Path("/mas-agent-kit"))


def version_path(start: Path | None = None) -> Path | None:
    seen: set[Path] = set()
    seeds: list[Path] = []
    if start is not None:
        seeds.append(Path(start).resolve())
    seeds.append(Path(__file__).resolve())
    seeds.extend(_EXTRA)
    for seed in seeds:
        folder = seed if seed.is_dir() else seed.parent
        for candidate in (folder, *folder.parents):
            path = candidate / "VERSION"
            if path in seen:
                continue
            seen.add(path)
            if path.is_file():
                return path
    return None


def read_mas_version(start: Path | None = None) -> str:
    path = version_path(start)
    if path is None:
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            return text
    return ""
