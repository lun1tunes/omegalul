#!/usr/bin/env python3
"""Validate tNavigator/ECL-style INCLUDE directives in SCHEDULE files.

The checker is deliberately read-only.  It does not rewrite INCLUDE paths or
the source files.  It checks the things that can produce the n8n materializer
error ``INCLUDE path invalid``:

* UTF-8/text encoding and embedded NUL bytes;
* an INCLUDE without a quoted, non-empty path;
* an unterminated quote or missing ``/`` terminator;
* absolute/URL paths and paths that escape the virtual package root; and
* missing include bodies when a package directory is supplied.

Examples:

    # Check one uploaded/flat root file.  No files are modified.
    python3 scripts/check_schedule_includes.py MVP1_schedule_IN.INC

    # Check the complete virtual package and resolve INCLUDE bodies.
    python3 scripts/check_schedule_includes.py \
        simulation-model-example/package \
        --root-path SCHEDULE/FORECAST/MONITORING_1_2_2_1_4q25_3_NORTH1_6_FDP.INC

    # Machine-readable output for CI/support bundles.
    python3 scripts/check_schedule_includes.py file.INC --json
"""

from __future__ import annotations

import argparse
import bisect
import codecs
import json
import posixpath
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


SUPPORTED_SUFFIXES = {".inc", ".data", ".sch", ".txt", ".grdecl"}
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
WINDOWS_ABS_RE = re.compile(r"^[A-Za-z]:/")


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    file: str
    line: int
    column: int
    message: str
    path: str | None = None


@dataclass(frozen=True)
class IncludeRef:
    line: int
    column: int
    path: str | None
    path_start: int
    path_end: int
    terminated: bool
    quote_closed: bool


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int
    quote_closed: bool = True


def line_starts(text: str) -> list[int]:
    starts = [0]
    starts.extend(index + 1 for index, char in enumerate(text) if char == "\n")
    return starts


def location(starts: list[int], offset: int, text_length: int) -> tuple[int, int]:
    # Do not clamp to the start of the last line: a one-line file (or a final
    # line without EOL) can legitimately report a column past that offset.
    offset = max(0, min(offset, text_length))
    row = bisect.bisect_right(starts, offset)
    return row, offset - starts[row - 1] + 1


def tokenize(text: str) -> list[Token]:
    """Tokenize enough of the deck grammar to inspect INCLUDE safely.

    ``--`` starts a comment outside a quoted string.  Newlines are retained so
    a normal multiline form (INCLUDE on one line, path on the next) works.
    """

    tokens: list[Token] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in " \t\r":
            index += 1
            continue
        if char == "\n":
            tokens.append(Token("newline", char, index, index + 1))
            index += 1
            continue
        if char == "-" and index + 1 < length and text[index + 1] == "-":
            newline = text.find("\n", index)
            index = length if newline < 0 else newline
            continue
        if char in "'\"":
            quote = char
            start = index
            index += 1
            value_start = index
            escaped = False
            while index < length:
                current = text[index]
                if escaped:
                    escaped = False
                    index += 1
                    continue
                if current == "\\":
                    escaped = True
                    index += 1
                    continue
                if current == quote:
                    value = text[value_start:index]
                    index += 1
                    tokens.append(Token("string", value, start, index, True))
                    break
                if current == "\n":
                    # A quoted filename cannot contain a physical newline.
                    value = text[value_start:index]
                    tokens.append(Token("string", value, start, index, False))
                    break
                index += 1
            else:
                tokens.append(Token("string", text[value_start:], start, length, False))
                index = length
            continue
        word = WORD_RE.match(text, index)
        if word:
            tokens.append(Token("word", word.group(0), word.start(), word.end()))
            index = word.end()
            continue
        if char == "/":
            tokens.append(Token("slash", char, index, index + 1))
        else:
            tokens.append(Token("other", char, index, index + 1))
        index += 1
    return tokens


def parse_includes(text: str) -> list[IncludeRef]:
    refs: list[IncludeRef] = []
    tokens = tokenize(text)
    for index, token in enumerate(tokens):
        if token.kind != "word" or token.value.upper() != "INCLUDE":
            continue

        cursor = index + 1
        while cursor < len(tokens) and tokens[cursor].kind == "newline":
            cursor += 1
        if cursor >= len(tokens):
            refs.append(IncludeRef(0, 0, None, token.end, token.end, False, False))
            continue

        candidate = tokens[cursor]
        if candidate.kind != "string":
            refs.append(IncludeRef(0, 0, None, candidate.start, candidate.end, False, False))
            continue

        cursor += 1
        while cursor < len(tokens) and tokens[cursor].kind == "newline":
            cursor += 1
        terminated = cursor < len(tokens) and tokens[cursor].kind == "slash"
        refs.append(
            IncludeRef(
                0,
                0,
                candidate.value,
                candidate.start + 1,
                max(candidate.start + 1, candidate.end - (1 if candidate.quote_closed else 0)),
                terminated,
                candidate.quote_closed,
            )
        )
    return refs


def clean_virtual_path(value: str) -> str:
    return value.strip().replace("\\", "/")


def is_absolute_or_url(value: str) -> bool:
    return value.startswith("/") or bool(URL_RE.match(value)) or bool(WINDOWS_ABS_RE.match(value))


def resolve_virtual_path(base: str, relative: str) -> tuple[str | None, str | None]:
    """Resolve a relative INCLUDE against a virtual package path."""

    value = clean_virtual_path(relative)
    if not value or "\x00" in value:
        return None, "invalid"
    if is_absolute_or_url(value):
        return None, "unsafe"
    base = clean_virtual_path(base)
    if not base or is_absolute_or_url(base):
        return None, "unsafe"
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base), value))
    if joined in {"", ".", ".."} or joined.startswith("../") or joined.startswith("/"):
        return None, "unsafe"
    return joined, None


def default_root_path(source: Path, text: str, package_dir: Path | None) -> str:
    if package_dir is not None:
        try:
            return source.resolve().relative_to(package_dir.resolve()).as_posix()
        except ValueError:
            pass
    # This mirrors the n8n materializer for a flat upload whose INCLUDE paths
    # contain Petrel's ../../... prefix.
    has_parent_include = any(
        ref.path and ".." in clean_virtual_path(ref.path).split("/")
        for ref in parse_includes(text)
    )
    name = source.name
    return f"SCHEDULE/FORECAST/{name}" if has_parent_include else name


class ScheduleIncludeChecker:
    def __init__(self, package_dir: Path | None = None) -> None:
        self.package_dir = package_dir.resolve() if package_dir else None
        self.findings: list[Finding] = []
        self.includes_checked = 0
        self.files_checked = 0
        self._visited: set[str] = set()

    def add(
        self,
        severity: str,
        code: str,
        source: Path,
        text: str,
        offset: int,
        message: str,
        path: str | None = None,
    ) -> None:
        starts = line_starts(text)
        line, column = location(starts, offset, len(text))
        self.findings.append(
            Finding(severity, code, str(source), line, column, message, path)
        )

    def read_text(self, source: Path) -> str:
        try:
            data = source.read_bytes()
        except OSError as exc:
            self.findings.append(
                Finding("error", "FILE_READ_ERROR", str(source), 1, 1, str(exc))
            )
            return ""

        if b"\x00" in data:
            self.findings.append(
                Finding(
                    "error",
                    "NUL_BYTE",
                    str(source),
                    1,
                    1,
                    "file contains a NUL byte; it is not a clean text deck",
                )
            )
        if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE, codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            self.findings.append(
                Finding(
                    "error",
                    "ENCODING_NOT_UTF8",
                    str(source),
                    1,
                    1,
                    "file has a UTF-16/UTF-32 BOM; save the deck as UTF-8 text",
                )
            )
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            self.findings.append(
                Finding(
                    "error",
                    "INVALID_UTF8",
                    str(source),
                    1,
                    1,
                    f"file is not valid UTF-8 ({exc})",
                )
            )
            text = data.decode("utf-8", errors="replace")
        return text

    def check_file(self, source: Path, virtual_path: str, recurse: bool = True) -> None:
        virtual_path = clean_virtual_path(virtual_path)
        visit_key = virtual_path.lower()
        if visit_key in self._visited:
            return
        self._visited.add(visit_key)
        self.files_checked += 1
        text = self.read_text(source)
        refs = parse_includes(text)
        for ref in refs:
            self.includes_checked += 1
            offset = ref.path_start if ref.path is not None else ref.path_start
            if ref.path is None:
                self.add(
                    "error",
                    "INCLUDE_PATH_MISSING",
                    source,
                    text,
                    offset,
                    "INCLUDE must be followed by a quoted path",
                )
                continue
            path = ref.path.strip()
            if not path:
                self.add(
                    "error",
                    "INCLUDE_PATH_INVALID",
                    source,
                    text,
                    ref.path_start,
                    "INCLUDE path is empty",
                    path,
                )
                continue
            if "\x00" in path:
                self.add(
                    "error",
                    "INCLUDE_PATH_INVALID",
                    source,
                    text,
                    ref.path_start,
                    "INCLUDE path contains a NUL byte",
                    path,
                )
                continue
            if not ref.quote_closed:
                self.add(
                    "error",
                    "INCLUDE_QUOTE_UNTERMINATED",
                    source,
                    text,
                    ref.path_start,
                    "INCLUDE path quote is not terminated",
                    path,
                )
                continue
            if not ref.terminated:
                self.add(
                    "error",
                    "INCLUDE_TERMINATOR_MISSING",
                    source,
                    text,
                    ref.path_end,
                    "INCLUDE must end with '/' after the quoted path",
                    path,
                )
            target, error = resolve_virtual_path(virtual_path, path)
            if error == "invalid":
                self.add(
                    "error",
                    "INCLUDE_PATH_INVALID",
                    source,
                    text,
                    ref.path_start,
                    "INCLUDE path is empty or malformed",
                    path,
                )
                continue
            if error == "unsafe" or target is None:
                self.add(
                    "error",
                    "INCLUDE_PATH_UNSAFE",
                    source,
                    text,
                    ref.path_start,
                    "INCLUDE path is absolute, a URL, or escapes the virtual package root",
                    path,
                )
                continue
            if self.package_dir is None or not recurse:
                continue
            target_file = self.package_dir.joinpath(*target.split("/"))
            try:
                target_file.resolve().relative_to(self.package_dir)
            except ValueError:
                self.add(
                    "error",
                    "INCLUDE_PATH_UNSAFE",
                    source,
                    text,
                    ref.path_start,
                    "resolved INCLUDE target escapes the package directory",
                    path,
                )
                continue
            if not target_file.is_file():
                self.add(
                    "error",
                    "INCLUDE_NOT_FOUND",
                    source,
                    text,
                    ref.path_start,
                    f"INCLUDE target does not exist: {target}",
                    path,
                )
                continue
            self.check_file(target_file, target)


def find_root_in_directory(directory: Path) -> tuple[Path, str] | None:
    candidates: list[tuple[Path, str]] = []
    for source in sorted(directory.rglob("*")):
        if not source.is_file() or source.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            text = source.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        if any(ref.path is not None for ref in parse_includes(text)):
            candidates.append((source, source.relative_to(directory).as_posix()))
    if len(candidates) == 1:
        return candidates[0]
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="root file or virtual package directory")
    parser.add_argument(
        "--root-path",
        help="virtual path of the root file, e.g. SCHEDULE/FORECAST/MVP1_schedule_IN.INC",
    )
    parser.add_argument(
        "--package-dir",
        type=Path,
        help="directory containing virtual package paths; enables INCLUDE_NOT_FOUND checks",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def run(args: argparse.Namespace) -> tuple[int, dict]:
    target = args.target.resolve()
    package_dir = args.package_dir.resolve() if args.package_dir else None
    if target.is_dir():
        if package_dir is None:
            package_dir = target
        if args.root_path:
            root_path = clean_virtual_path(args.root_path)
            source = package_dir.joinpath(*root_path.split("/"))
        else:
            detected = find_root_in_directory(package_dir)
            if detected is None:
                result = {
                    "ok": False,
                    "error": "could not identify a unique root file; pass --root-path",
                }
                return 2, result
            source, root_path = detected
    else:
        source = target
        root_path = clean_virtual_path(args.root_path or "")
        if not root_path:
            # Read once only for root-path inference; check_file performs the
            # authoritative read and reports encoding/NUL problems.
            try:
                preview = source.read_bytes().decode("utf-8-sig", errors="replace")
            except OSError:
                preview = ""
            root_path = default_root_path(source, preview, package_dir)

    if not source.is_file():
        result = {"ok": False, "error": f"root file not found: {source}"}
        return 2, result

    checker = ScheduleIncludeChecker(package_dir)
    checker.check_file(source, root_path)
    errors = [finding for finding in checker.findings if finding.severity == "error"]
    result = {
        "ok": not errors,
        "root_file": str(source),
        "root_path": root_path,
        "package_dir": str(package_dir) if package_dir else None,
        "files_checked": checker.files_checked,
        "includes_checked": checker.includes_checked,
        "findings": [asdict(finding) for finding in checker.findings],
    }
    return (0 if not errors else 1), result


def print_human(result: dict) -> None:
    print("PASS" if result.get("ok") else "FAIL")
    if result.get("error"):
        print(f"ERROR: {result['error']}")
        return
    for finding in result.get("findings", []):
        location_text = f"{finding['file']}:{finding['line']}:{finding['column']}"
        path_text = f" [{finding['path']}]" if finding.get("path") else ""
        print(f"{finding['severity'].upper()} {finding['code']} {location_text}{path_text}: {finding['message']}")
    print(
        f"Checked files={result['files_checked']}, "
        f"INCLUDEs={result['includes_checked']}, "
        f"findings={len(result.get('findings', []))}"
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    code, result = run(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
