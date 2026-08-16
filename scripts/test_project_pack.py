#!/usr/bin/env python3
"""Smoke tests for scripts/project_pack.py (no pytest required)."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "project_pack",
        ROOT / "scripts" / "project_pack.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pack_unpack_roundtrip() -> None:
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        out = Path(tmp) / "out"
        archive = Path(tmp) / "all.txt"
        (src / "n8n" / "workflows").mkdir(parents=True)
        (src / "excel-agent-tools").mkdir(parents=True)
        (src / "scripts").mkdir(parents=True)
        (src / ".venv" / "lib").mkdir(parents=True)
        (src / "excel-agent-tools" / "tests").mkdir(parents=True)

        (src / "docs.md").write_text("# runbook\n", encoding="utf-8")
        (src / ".env.example").write_text("KEY=value\n", encoding="utf-8")
        (src / "n8n" / "workflows" / "a.workflow.json").write_text('{"ok":1}\n', encoding="utf-8")
        (src / "excel-agent-tools" / "app.py").write_text("print(1)\n", encoding="utf-8")
        (src / "excel-agent-tools" / "secret.env").write_text("SECRET=1\n", encoding="utf-8")
        (src / "excel-agent-tools" / "tests" / "test_x.py").write_text("assert True\n", encoding="utf-8")
        (src / ".venv" / "lib" / "skip.py").write_text("nope\n", encoding="utf-8")
        (src / "scripts" / "project_pack.py").write_text("# pack\n", encoding="utf-8")
        # binary sample
        (src / "n8n" / "blob.bin").write_bytes(b"\x00\xff")
        # .bin not in include list — skip; use .inc with binary via forced name
        (src / "n8n" / "sample.inc").write_bytes(b"DATES\n/\n\x00")

        # No .git → walk fallback
        files = mod.collect_files(src)
        rels = [p.relative_to(src).as_posix() for p in files]
        assert "docs.md" in rels
        assert ".env.example" in rels
        assert "n8n/workflows/a.workflow.json" in rels
        assert "excel-agent-tools/app.py" in rels
        assert "scripts/project_pack.py" in rels
        assert "excel-agent-tools/secret.env" not in rels
        assert "excel-agent-tools/tests/test_x.py" not in rels
        assert ".venv/lib/skip.py" not in rels
        assert "n8n/sample.inc" in rels

        mod.pack(src, archive)
        mod.unpack(out, archive)
        assert (out / "docs.md").read_text(encoding="utf-8") == "# runbook\n"
        assert (out / "n8n" / "workflows" / "a.workflow.json").read_text(encoding="utf-8") == '{"ok":1}\n'
        assert (out / "n8n" / "sample.inc").read_bytes() == b"DATES\n/\n\x00"


def test_split_join() -> None:
    mod = _load()
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        archive = Path(tmp) / "all.txt"
        joined = Path(tmp) / "joined.txt"
        (src / "scripts").mkdir(parents=True)
        (src / "docs.md").write_text("x" * 5000, encoding="utf-8")
        (src / "scripts" / "a.py").write_text("print(1)\n", encoding="utf-8")
        mod.pack(src, archive)
        chunks = mod.split_archive(archive, chunk_size=800)
        assert len(chunks) >= 2
        mod.join_archive(joined, chunk_dir=archive.parent, chunk_prefix="all")
        assert joined.read_bytes() == archive.read_bytes()


def main() -> int:
    test_pack_unpack_roundtrip()
    test_split_join()
    print("project_pack self-tests OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
