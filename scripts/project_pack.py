#!/usr/bin/env python3
"""Pack and unpack the MAS deployment tree into a portable text archive."""
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

PACK_DIRS = (
    "excel-agent-tools",
    "fastapi-math-service",
    "mas-activity-service",
    "n8n",
    "schedule-builder-service",
    "scripts",
)
ROOT_FILES = (".env.example", "docs.md", "README.md", "docker-compose.yml", ".gitignore")
NEVER_PACK_DIR_PREFIXES = ("simulation-model-example/", "context-seeder/")
EXCLUDED_DIR_NAMES = {
    ".git", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".idea", ".cursor", "node_modules", "data", "task_binaries",
}
EXCLUDED_FILE_NAMES = {ARCHIVE_FILE, "conftest.py", ".coverage"}
INCLUDED_FILE_EXTENSIONS = {
    ".py", ".md", ".txt", ".json", ".js", ".css", ".html", ".csv", ".yml",
    ".yaml", ".toml", ".ini", ".cfg", ".bat", ".sh", ".inc", ".sch",
    ".grdecl", ".example", ".svg",
}
INCLUDED_FILE_NAMES = {
    "Dockerfile", ".dockerignore", ".gitattributes", ".gitignore",
    "requirements.txt", "requirements-dev.txt",
}


def _is_under_pack_root(rel: Path) -> bool:
    posix = rel.as_posix()
    if posix.startswith(NEVER_PACK_DIR_PREFIXES):
        return False
    if posix in ROOT_FILES:
        return True
    return bool(rel.parts) and rel.parts[0] in PACK_DIRS


def should_skip_path(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    if not _is_under_pack_root(rel):
        return True
    if set(rel.parts) & EXCLUDED_DIR_NAMES:
        return True
    if "tests" in rel.parts and rel.parts[0] not in {"n8n", "schedule-builder-service"}:
        return True
    if path.name in EXCLUDED_FILE_NAMES or path.name.endswith("_test.py"):
        return True
    if path.name.startswith("test_") and path.suffix.lower() == ".py":
        return rel.parts[0] in {"excel-agent-tools", "mas-activity-service"}
    if path.suffix == ".env" and not path.name.endswith(".env.example"):
        return True
    return False


def should_include_path(path: Path) -> bool:
    return path.name in INCLUDED_FILE_NAMES or path.name.endswith(".env.example") or path.suffix.lower() in INCLUDED_FILE_EXTENSIONS


def _git_list_candidates(root: Path) -> list[Path] | None:
    if not (root / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
             "--exclude-standard", "--", *PACK_DIRS, *ROOT_FILES],
            check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    result = []
    for raw in proc.stdout.split(b"\0"):
        if raw:
            path = root / raw.decode("utf-8", errors="surrogateescape")
            if path.is_file():
                result.append(path)
    return result


def _git_ignored(root: Path, path: Path) -> bool:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", "--", str(path.relative_to(root))],
            capture_output=True,
        ).returncode == 0
    except OSError:
        return False


def collect_files(root: Path, *, archive_path: Path | None = None) -> list[Path]:
    archive_paths = {(root / ARCHIVE_FILE).resolve()}
    if archive_path is not None:
        archive_paths.add(archive_path.resolve())
    candidates = _git_list_candidates(root)
    from_git = candidates is not None
    if candidates is None:
        candidates = [p for p in root.rglob("*") if p.is_file()]
    files = []
    for path in candidates:
        if should_skip_path(path, root) or not should_include_path(path):
            continue
        if path.resolve() in archive_paths:
            continue
        if not from_git and _git_ignored(root, path):
            continue
        files.append(path)
    return sorted({p.resolve(): p for p in files}.values(),
                  key=lambda p: p.relative_to(root).as_posix())


def _read_payload(path: Path) -> tuple[str, str]:
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
    if encoding == "utf8":
        path.write_text(payload, encoding="utf-8")
    elif encoding == "base64":
        path.write_bytes(base64.b64decode(payload.encode("ascii")))
    else:
        raise ValueError(f"Unknown payload encoding: {encoding!r}")


def pack(root: Path, output_file: Path) -> None:
    files = collect_files(root, archive_path=output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8", newline="") as out:
        for path in files:
            rel = path.relative_to(root).as_posix()
            encoding, content = _read_payload(path)
            out.write(f"{BEGIN}\t{rel}\t{len(content)}\t{encoding}\n")
            out.write(content)
            out.write("\n")
            out.write(f"{END}\n")
    print(f"Packed {len(files)} files into {output_file}")


def _chunk_pattern(prefix: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(prefix)}([1-9][0-9]*)$")


def split_archive(input_file: Path, *, chunk_dir: Path | None = None,
                   chunk_prefix: str | None = None,
                   chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[Path]:
    if not input_file.exists():
        raise FileNotFoundError(f"Archive file not found: {input_file}")
    if chunk_size <= 0:
        raise ValueError("Chunk size must be positive")
    target = chunk_dir or input_file.parent
    target.mkdir(parents=True, exist_ok=True)
    prefix = chunk_prefix or input_file.stem
    pattern = _chunk_pattern(prefix)
    for path in target.iterdir():
        if path.is_file() and pattern.fullmatch(path.name):
            path.unlink()
    chunks: list[Path] = []
    current = None
    current_size = 0
    with input_file.open("r", encoding="utf-8", newline="") as source:
        for line in source:
            size = len(line.encode("utf-8"))
            if current is None or current_size and current_size + size > chunk_size:
                if current is not None:
                    current.close()
                chunk_path = target / f"{prefix}{len(chunks) + 1}"
                current = chunk_path.open("w", encoding="utf-8", newline="")
                chunks.append(chunk_path)
                current_size = 0
            current.write(line)
            current_size += size
    if current is None:
        path = target / f"{prefix}1"
        path.write_text("", encoding="utf-8")
        chunks.append(path)
    else:
        current.close()
    return chunks


def _collect_chunks(directory: Path, prefix: str) -> list[Path]:
    matches = []
    pattern = _chunk_pattern(prefix)
    for path in directory.iterdir():
        match = pattern.fullmatch(path.name)
        if path.is_file() and match:
            matches.append((int(match.group(1)), path))
    matches.sort()
    if not matches:
        raise FileNotFoundError(f"Archive chunks not found in {directory} with prefix {prefix!r}")
    if [n for n, _ in matches] != list(range(1, len(matches) + 1)):
        raise ValueError("Archive chunks are incomplete or out of order")
    return [path for _, path in matches]


def join_archive(output_file: Path, *, chunk_dir: Path | None = None,
                 chunk_prefix: str | None = None) -> list[Path]:
    directory = chunk_dir or output_file.parent
    chunks = _collect_chunks(directory, chunk_prefix or output_file.stem)
    output_file.write_bytes(b"".join(path.read_bytes() for path in chunks))
    return chunks


def unpack(root: Path, input_file: Path) -> None:
    restored = 0
    root_resolved = root.resolve()
    with input_file.open("r", encoding="utf-8") as source:
        while True:
            header = source.readline()
            if not header:
                break
            if not header.startswith(BEGIN + "\t"):
                raise ValueError("Invalid archive format: malformed BEGIN header")
            fields = header.rstrip("\n").split("\t")
            if len(fields) == 3:
                _, rel_path, raw_len = fields
                encoding = "utf8"
            elif len(fields) == 4:
                _, rel_path, raw_len, encoding = fields
            else:
                raise ValueError("Invalid archive format: malformed BEGIN payload")
            content = source.read(int(raw_len))
            if len(content) != int(raw_len) or source.read(1) != "\n" or source.readline().rstrip("\n") != END:
                raise ValueError("Invalid archive format: truncated file or missing END marker")
            target = (root / rel_path).resolve()
            if root_resolved != target and root_resolved not in target.parents:
                raise ValueError(f"Refusing to unpack outside root: {rel_path}")
            _write_payload(target, encoding, content)
            restored += 1
    print(f"Restored {restored} files from {input_file}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("pack", "unpack", "split", "join"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--archive", default=None)
    parser.add_argument("--chunk-dir", default=None)
    parser.add_argument("--chunk-prefix", default=None)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    archive = Path(args.archive).resolve() if args.archive else root / ARCHIVE_FILE
    chunk_dir = Path(args.chunk_dir).resolve() if args.chunk_dir else archive.parent
    if args.mode == "pack":
        pack(root, archive)
    elif args.mode == "unpack":
        unpack(root, archive)
    elif args.mode == "split":
        split_archive(archive, chunk_dir=chunk_dir, chunk_prefix=args.chunk_prefix, chunk_size=args.chunk_size)
    else:
        join_archive(archive, chunk_dir=chunk_dir, chunk_prefix=args.chunk_prefix)


if __name__ == "__main__":
    main()
