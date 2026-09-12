"""α3 field pack: VERSION, field_check, mas_gate --bundle (E6)."""
from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def version_mod():
    return _load("mas_version")


@pytest.fixture(scope="module")
def field_mod():
    return _load("field_check")


@pytest.fixture(scope="module")
def gate():
    return _load("mas_gate")


def test_read_mas_version_matches_version_file(version_mod) -> None:
    expected = ROOT.joinpath("VERSION").read_text(encoding="utf-8").splitlines()[0].strip()
    assert expected
    assert version_mod.read_mas_version(ROOT) == expected
    assert version_mod.read_mas_version() == expected


def test_health_ok_and_health_version(field_mod) -> None:
    assert field_mod.health_ok({"ok": True, "mas_version": "0.8.0"})
    assert field_mod.health_ok({"status": "ok"})
    assert field_mod.health_ok({"status": "ready"})
    assert not field_mod.health_ok({"status": "down"})
    assert not field_mod.health_ok(None)
    assert field_mod.health_version({"mas_version": "0.8.0", "version": "0.7.0"}) == "0.8.0"
    assert field_mod.health_version({"version": "0.7.0"}) == "0.7.0"
    assert field_mod.health_version({}) == ""


def test_check_once_pass_and_russian_table(field_mod, monkeypatch) -> None:
    ver = field_mod.read_mas_version()

    def fake_fetch(url: str):
        if url.endswith("/ready"):
            return 200, {"ready": True, "status": "ready"}, ""
        return 200, {"status": "ok", "mas_version": ver}, ""

    monkeypatch.setattr(field_mod, "fetch_json", fake_fetch)
    ok, rows, expected = field_mod.check_once()
    assert ok and expected == ver and len(rows) == 4
    assert all(r["ok"] and r["health"] == "OK" for r in rows)
    assert rows[3]["ready"] == "OK"
    table = field_mod.format_table(rows, expected=expected, ok=ok)
    assert "Сервис" in table and "/health" in table and "версия" in table
    assert "Итог: PASS" in table


def test_check_once_fails_on_version_mismatch_and_down(field_mod, monkeypatch) -> None:
    ver = field_mod.read_mas_version()

    def fake_fetch(url: str):
        if "8000" in url and url.endswith("/health"):
            return 200, {"status": "ok", "mas_version": "0.0.0"}, ""
        if url.endswith("/ready"):
            return 200, {"ready": True}, ""
        if "8100" in url:
            return 0, None, "timed out"
        return 200, {"status": "ok", "mas_version": ver}, ""

    monkeypatch.setattr(field_mod, "fetch_json", fake_fetch)
    ok, rows, expected = field_mod.check_once()
    assert not ok and expected == ver
    excel = next(r for r in rows if r["title"] == "Excel Tools")
    math = next(r for r in rows if r["title"] == "Math")
    assert not excel["ok"] and "0.0.0" in excel["note"]
    assert not math["ok"] and math["health"] == "FAIL"
    table = field_mod.format_table(rows, expected=expected, ok=ok)
    assert "Итог: FAIL" in table


def test_missing_version_file_fails(field_mod, monkeypatch) -> None:
    monkeypatch.setattr(field_mod, "read_mas_version", lambda start=None: "")

    def fake_fetch(url: str):
        if url.endswith("/ready"):
            return 200, {"ready": True}, ""
        return 200, {"status": "ok", "mas_version": "0.8.0"}, ""

    monkeypatch.setattr(field_mod, "fetch_json", fake_fetch)
    ok, rows, expected = field_mod.check_once()
    assert not ok and expected == ""
    assert all(not r["ok"] for r in rows)


def test_bundle_zip_members_and_env_stamp(gate, tmp_path: Path) -> None:
    ver = gate.repo_version()
    assert ver
    zip_path = gate.build_bundle(tmp_path)
    assert zip_path == tmp_path / f"mas-{ver}.zip"
    prefix = f"mas-{ver}"
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        order = gate.runtime_import_order()
        assert f"{prefix}/VERSION" in names
        assert f"{prefix}/IMPORT_ORDER.txt" in names
        assert f"{prefix}/PACK.txt" in names
        assert f"{prefix}/docs.md" in names
        assert f"{prefix}/check-all-windows.bat" in names
        assert f"{prefix}/scripts/field_check.py" in names
        assert f"{prefix}/scripts/mas_version.py" in names
        assert f"{prefix}/.env.example" in names
        for i, rel in enumerate(order, 1):
            assert f"{prefix}/workflows/{i:02d}-{Path(rel).name}" in names
        env = zf.read(f"{prefix}/.env.example").decode("utf-8")
        assert f"MAS_VERSION={ver}" in env
        pack = zf.read(f"{prefix}/PACK.txt").decode("utf-8")
        assert "IMPORT_ORDER.txt" in pack and ver in pack
        listed = zf.read(f"{prefix}/IMPORT_ORDER.txt").decode("utf-8")
        for rel in order:
            assert Path(rel).name in listed


def test_runtime_config_mas_version_assignment(gate) -> None:
    wf = json.loads((ROOT / "n8n" / "workflows" / "core" / "mas-runtime-config.workflow.json").read_text(encoding="utf-8"))
    urls = next(node for node in wf["nodes"] if node["name"] == "Runtime URLs")
    fields = {item["name"]: item["value"] for item in urls["parameters"]["assignments"]["assignments"]}
    assert fields["mas_version"] == gate.repo_version()


def test_check_all_windows_bat_calls_field_check() -> None:
    text = (ROOT / "check-all-windows.bat").read_text(encoding="utf-8")
    assert "scripts\\field_check.py" in text
