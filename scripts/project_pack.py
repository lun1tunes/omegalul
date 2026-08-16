#!/usr/bin/env python3
"""Pack / unpack the MAS field-deploy tree into a single all.txt (pywp-style).

Packs only deploy roots (and a few root files), skips anything matched by
.gitignore / common junk, then restores the same relative paths on unpack.

Usage (lab / home):
  python3 scripts/project_pack.py pack
  python3 scripts/project_pack.py split          # optional: all1, all2, … for transfer
  # copy scripts/project_pack.py + all.txt (or chunks) to the work machine

Usage (work):
  python3 project_pack.py join                  # if you brought chunks
  python3 project_pack.py unpack
  # then follow docs.md Step 0 → 8
"""
from __future__ import annotations

import argparse
import base64
import re
import subprocess
from pathlib import Path

ARCHIVE_FILE = "all.txt"
BEGIN = "===BEGIN_FILE==="
END = "===END_FILE==="
DEFAULT_CHUNK_SIZE = 1_000_000

# Field-deploy surface (see docs.md). simulation-model / context-seeder stay out.
PACK_DIRS = (
    "excel-agent-tools",
    "fastapi-math-service",
    "mas-activity-service",
    "n8n",
    "scripts",
)

# Never pack even if someone adds the path by mistake.
NEVER_PACK_DIR_PREFIXES = (
    "simulation-model-example/",
    "context-seeder/",
)

ROOT_FILES = (
    ".env.example",
    "docs.md",
    "README.md",
    "docker-compose.yml",
    ".gitignore",
)

EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".cursor",
    "node_modules",
    "data",  # Activity local state
    "task_binaries",
}

EXCLUDED_FILE_NAMES = {
    ARCHIVE_FILE,
    "conftest.py",
    ".coverage",
}

# Text / binary / config needed to import workflows and start Windows services.
INCLUDED_FILE_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".js",
    ".css",
    ".html",
    ".csv",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".bat",
    ".sh",
    ".inc",
    ".sch",
    ".grdecl",
    ".example",
    ".svg",
}

INCLUDED_FILE_NAMES = {
    "Dockerfile",
    ".dockerignore",
    ".gitattributes",
    ".gitignore",
    "requirements.txt",
    "requirements-dev.txt",
}


def _is_under_pack_root(rel: Path) -> bool:
    posix = rel.as_posix()
    if posix.startswith(NEVER_PACK_DIR_PREFIXES) or posix in {
        p.rstrip("/") for p in NEVER_PACK_DIR_PREFIXES
    }:
        return False
    if posix in ROOT_FILES:
        return True
    if not rel.parts:
        return False
    return rel.parts[0] in PACK_DIRS


def should_skip_path(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if not _is_under_pack_root(rel):
        return True
    parts = set(rel.parts)
    if parts & EXCLUDED_DIR_NAMES:
        return True
    # Keep n8n/tests smokes (lab gate); skip other project test trees.
    if "tests" in rel.parts and rel.parts[0] != "n8n":
        return True
    if path.name in EXCLUDED_FILE_NAMES:
        return True
    if path.name.startswith("test_") and path.suffix.lower() == ".py" and rel.parts[0] != "n8n":
        return True
    if path.name.endswith("_test.py"):
        return True
    # Local env secrets (also in .gitignore).
    if path.suffix == ".env" or path.name.endswith(".env"):
        if path.name.endswith(".env.example"):
            return False
        return True
    return False


def should_include_path(path: Path) -> bool:
    if path.name in INCLUDED_FILE_NAMES:
        return True
    if path.name.endswith(".env.example"):
        return True
    return path.suffix.lower() in INCLUDED_FILE_EXTENSIONS


def _git_list_candidates(root: Path) -> list[Path] | None:
    """Tracked + untracked non-ignored files under pack roots (None if not a git repo)."""
    if not (root / ".git").exists():
        return None
    pathspecs = list(PACK_DIRS) + list(ROOT_FILES)
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                *pathspecs,
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    out: list[Path] = []
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="surrogateescape")
        path = root / rel
        if path.is_file():
            out.append(path)
    return out


def _git_ignored(root: Path, path: Path) -> bool:
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "check-ignore",
                "-q",
                "--",
                str(path.relative_to(root)),
            ],
            capture_output=True,
        )
        return proc.returncode == 0
    except OSError:
        return False


def collect_files(root: Path, *, archive_path: Path | None = None) -> list[Path]:
    archive_resolved = archive_path.resolve() if archive_path is not None else None
    default_archive_resolved = (root / ARCHIVE_FILE).resolve()

    git_candidates = _git_list_candidates(root)
    if git_candidates is None:
        candidates = [p for p in root.rglob("*") if p.is_file()]
        use_git_ignore_check = True
    else:
        candidates = git_candidates
        use_git_ignore_check = False

    files: list[Path] = []
    for path in candidates:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if should_skip_path(path, root):
            continue
        if not should_include_path(path):
            continue
        resolved = path.resolve()
        if resolved == default_archive_resolved:
            continue
        if archive_resolved is not None and resolved == archive_resolved:
            continue
        if use_git_ignore_check and _git_ignored(root, path):
            continue
        files.append(path)

    unique = {p.resolve(): p for p in files}
    return sorted(unique.values(), key=lambda p: p.relative_to(root).as_posix())


def _read_payload(path: Path) -> tuple[str, str]:
    """Return (encoding_tag, payload_text). encoding_tag is 'utf8' or 'base64'."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "base64", base64.b64encode(raw).decode("ascii")
    if "\0" in text:
        return "base64", base64.b64encode(raw).decode("ascii")
    return "utf8", text


def _write_payload(path: Path, encoding: str, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if encoding == "base64":
        path.write_bytes(base64.b64decode(payload.encode("ascii")))
    elif encoding == "utf8":
        path.write_text(payload, encoding="utf-8")
    else:
        raise ValueError(f"Unknown payload encoding: {encoding!r}")


def _chunk_prefix_for_archive(archive_path: Path) -> str:
    return archive_path.stem


def _chunk_name_pattern(chunk_prefix: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(chunk_prefix)}([1-9][0-9]*)$")


def _remove_existing_chunk_files(chunk_dir: Path, chunk_prefix: str) -> None:
    pattern = _chunk_name_pattern(chunk_prefix)
    if not chunk_dir.is_dir():
        return
    for path in chunk_dir.iterdir():
        if path.is_file() and pattern.fullmatch(path.name):
            path.unlink()


def collect_chunk_files(chunk_dir: Path, chunk_prefix: str) -> list[Path]:
    pattern = _chunk_name_pattern(chunk_prefix)
    matches: list[tuple[int, Path]] = []
    for path in chunk_dir.iterdir():
        if not path.is_file():
            continue
        match = pattern.fullmatch(path.name)
        if match is None:
            continue
        matches.append((int(match.group(1)), path))
    if not matches:
        raise FileNotFoundError(
            f"Archive chunks not found in {chunk_dir} with prefix {chunk_prefix!r}"
        )
    matches.sort(key=lambda item: item[0])
    for expected_index, (actual_index, path) in enumerate(matches, start=1):
        if actual_index != expected_index:
            raise ValueError(
                "Archive chunks are incomplete or out of order: expected "
                f"{chunk_prefix}{expected_index}, found {path.name}"
            )
    return [path for _, path in matches]


def pack(root: Path, output_file: Path) -> None:
    files = collect_files(root, archive_path=output_file)
    with output_file.open("w", encoding="utf-8", newline="") as out:
        for path in files:
            rel = path.relative_to(root).as_posix()
            encoding, content = _read_payload(path)
            out.write(f"{BEGIN}\t{rel}\t{len(content)}\t{encoding}\n")
            out.write(content)
            out.write("\n")
            out.write(f"{END}\n")
    print(f"Packed {len(files)} files into {output_file}")


def split_archive(
    input_file: Path,
    *,
    chunk_dir: Path | None = None,
    chunk_prefix: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> list[Path]:
    if not input_file.exists():
        raise FileNotFoundError(f"Archive file not found: {input_file}")
    if chunk_size <= 0:
        raise ValueError("Chunk size must be a positive integer")

    target_dir = input_file.parent if chunk_dir is None else chunk_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    prefix = (
        _chunk_prefix_for_archive(input_file)
        if chunk_prefix is None
        else str(chunk_prefix)
    )
    _remove_existing_chunk_files(target_dir, prefix)

    chunk_paths: list[Path] = []
    chunk_file = None
    chunk_path: Path | None = None
    chunk_bytes = 0
    chunk_index = 0

    try:
        with input_file.open("r", encoding="utf-8", newline="") as src:
            for line in src:
                line_bytes = len(line.encode("utf-8"))
                if chunk_file is None or (
                    chunk_bytes > 0 and chunk_bytes + line_bytes > chunk_size
                ):
                    if chunk_file is not None and chunk_path is not None:
                        chunk_file.close()
                        chunk_paths.append(chunk_path)
                    chunk_index += 1
                    chunk_path = target_dir / f"{prefix}{chunk_index}"
                    chunk_file = chunk_path.open("w", encoding="utf-8", newline="")
                    chunk_bytes = 0
                assert chunk_file is not None
                chunk_file.write(line)
                chunk_bytes += line_bytes

        if chunk_file is None:
            chunk_index = 1
            chunk_path = target_dir / f"{prefix}{chunk_index}"
            chunk_file = chunk_path.open("w", encoding="utf-8", newline="")

        if chunk_file is not None:
            chunk_file.close()
            if chunk_path is not None:
                chunk_paths.append(chunk_path)
    finally:
        if chunk_file is not None and not chunk_file.closed:
            chunk_file.close()

    print(
        f"Split {input_file} into {len(chunk_paths)} chunks in {target_dir} "
        f"with prefix {prefix!r}"
    )
    return chunk_paths


def join_archive(
    output_file: Path,
    *,
    chunk_dir: Path | None = None,
    chunk_prefix: str | None = None,
) -> list[Path]:
    source_dir = output_file.parent if chunk_dir is None else chunk_dir
    prefix = (
        _chunk_prefix_for_archive(output_file)
        if chunk_prefix is None
        else str(chunk_prefix)
    )
    chunk_paths = collect_chunk_files(source_dir, prefix)
    with output_file.open("wb") as out:
        for chunk_path in chunk_paths:
            out.write(chunk_path.read_bytes())
    print(f"Joined {len(chunk_paths)} chunks into {output_file}")
    return chunk_paths


def unpack(root: Path, input_file: Path) -> None:
    if not input_file.exists():
        raise FileNotFoundError(f"Archive file not found: {input_file}")

    restored = 0
    root_resolved = root.resolve()
    with input_file.open("r", encoding="utf-8") as src:
        while True:
            header = src.readline()
            if not header:
                break
            if not header.startswith(f"{BEGIN}\t"):
                raise ValueError("Invalid archive format: malformed BEGIN header")

            payload = header.rstrip("\n").split("\t")
            # New: BEGIN \t rel \t len \t encoding
            # Old pywp-compatible: BEGIN \t rel \t len  → utf8
            if len(payload) == 3:
                _, rel_path, raw_len = payload
                encoding = "utf8"
            elif len(payload) == 4:
                _, rel_path, raw_len, encoding = payload
            else:
                raise ValueError("Invalid archive format: malformed BEGIN payload")
            content_len = int(raw_len)

            content = src.read(content_len)
            if len(content) != content_len:
                raise ValueError("Invalid archive format: truncated file content")

            separator = src.read(1)
            if separator != "\n":
                raise ValueError("Invalid archive format: missing content separator")

            end_line = src.readline().rstrip("\n")
            if end_line != END:
                raise ValueError("Invalid archive format: missing END marker")

            target = (root / rel_path).resolve()
            if root_resolved != target and root_resolved not in target.parents:
                raise ValueError(f"Refusing to unpack outside root: {rel_path}")
            _write_payload(target, encoding, content)
            restored += 1

    print(f"Restored {restored} files from {input_file}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Pack MAS deploy tree (excel-agent-tools, fastapi-math-service, "
            "mas-activity-service, n8n, scripts, .env.example, docs.md) into all.txt; "
            "split/join for transfer; unpack on the work machine."
        )
    )
    parser.add_argument(
        "mode",
        choices=("pack", "unpack", "split", "join"),
        help=(
            "pack: create all.txt; unpack: restore; "
            "split: chunk all.txt; join: rebuild all.txt from chunks"
        ),
    )
    parser.add_argument("--root", default=".", help="Project root (default: .)")
    parser.add_argument(
        "--archive",
        default=None,
        help=f"Archive path (default: <root>/{ARCHIVE_FILE})",
    )
    parser.add_argument(
        "--chunk-dir",
        default=None,
        help="Chunk directory (default: archive directory)",
    )
    parser.add_argument(
        "--chunk-prefix",
        default=None,
        help="Chunk prefix (default: archive stem, e.g. 'all')",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Max chunk bytes for split (default: {DEFAULT_CHUNK_SIZE})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(args.root).resolve()
    archive = (
        (root / ARCHIVE_FILE) if args.archive is None else Path(args.archive).resolve()
    )
    chunk_dir = archive.parent if args.chunk_dir is None else Path(args.chunk_dir).resolve()

    if args.mode == "pack":
        pack(root, archive)
    elif args.mode == "split":
        split_archive(
            archive,
            chunk_dir=chunk_dir,
            chunk_prefix=args.chunk_prefix,
            chunk_size=args.chunk_size,
        )
    elif args.mode == "join":
        join_archive(
            archive,
            chunk_dir=chunk_dir,
            chunk_prefix=args.chunk_prefix,
        )
    else:
        unpack(root, archive)


if __name__ == "__main__":
    main()
