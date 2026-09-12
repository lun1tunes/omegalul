#!/usr/bin/env python3
"""Windows field check: four FastAPI /health + Activity /ready. Stdlib only.

Usage (repo root or unpacked pack):
  python scripts/field_check.py
  check-all-windows.bat

URLs (env, else 127.0.0.1): EXCEL_TOOLS_URL, SCHEDULE_SERVICE_URL, MATH_URL, ACTIVITY_URL.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from mas_version import read_mas_version  # noqa: E402

TIMEOUT_S = 5
PROBES = (
    ("Excel Tools", "EXCEL_TOOLS_URL", "http://127.0.0.1:8000", False),
    ("Schedule Builder", "SCHEDULE_SERVICE_URL", "http://127.0.0.1:8090", False),
    ("Math", "MATH_URL", "http://127.0.0.1:8100", False),
    ("Activity", "ACTIVITY_URL", "http://127.0.0.1:8200", True),
)


def _url(env_key: str, default: str) -> str:
    return (os.environ.get(env_key) or default).strip().rstrip("/")


def fetch_json(url: str) -> tuple[int, dict | None, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = int(getattr(resp, "status", None) or resp.getcode() or 0)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return int(exc.code or 0), _maybe_json(raw), f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return 0, None, str(exc)[:120]
    body = _maybe_json(raw)
    if body is None:
        return code, None, f"HTTP {code} не JSON"
    return code, body, ""


def _maybe_json(raw: str) -> dict | None:
    try:
        data = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def health_ok(body: dict | None) -> bool:
    if not isinstance(body, dict):
        return False
    if body.get("ok") is True:
        return True
    return str(body.get("status") or "").lower() in {"ok", "ready"}


def health_version(body: dict | None) -> str:
    if not isinstance(body, dict):
        return ""
    return str(body.get("mas_version") or body.get("version") or "").strip()


def ready_ok(code: int, body: dict | None) -> bool:
    if code != 200 or not isinstance(body, dict):
        return False
    if body.get("ready") is True:
        return True
    return str(body.get("status") or "").lower() == "ready"


def check_once(*, expected: str = "") -> tuple[bool, list[dict], str]:
    expected = expected or read_mas_version()
    rows: list[dict] = []
    all_ok = True
    for title, env_key, default, want_ready in PROBES:
        base = _url(env_key, default)
        h_code, h_body, h_err = fetch_json(base + "/health")
        h_pass = h_code == 200 and health_ok(h_body)
        got_ver = health_version(h_body)
        ver_pass = bool(expected) and got_ver == expected
        ready_pass = True
        ready_note = "—"
        if want_ready:
            r_code, r_body, r_err = fetch_json(base + "/ready")
            ready_pass = ready_ok(r_code, r_body)
            ready_note = "OK" if ready_pass else (r_err or f"HTTP {r_code}")
        note = ""
        if not h_pass:
            note = h_err or f"HTTP {h_code}"
        elif not ver_pass:
            note = f"версия {got_ver or 'нет'} ≠ {expected}"
        ok = h_pass and ver_pass and ready_pass
        all_ok = all_ok and ok
        rows.append(
            {
                "title": title,
                "url": base,
                "health": "OK" if h_pass else "FAIL",
                "ready": ready_note if want_ready else "—",
                "version": got_ver or "—",
                "expected": expected or "—",
                "ok": ok,
                "note": note,
            }
        )
    return all_ok, rows, expected


def format_table(rows: list[dict], *, expected: str, ok: bool) -> str:
    headers = ("Сервис", "/health", "/ready", "версия", "ожидание")
    cols = [
        [r["title"] for r in rows],
        [r["health"] for r in rows],
        [r["ready"] for r in rows],
        [r["version"] for r in rows],
        [r["expected"] for r in rows],
    ]
    widths = [max(len(headers[i]), max((len(str(v)) for v in col), default=0)) for i, col in enumerate(cols)]
    def fmt(cells: tuple[str, ...]) -> str:
        return "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells))
    lines = [fmt(headers), fmt(tuple("-" * w for w in widths))]
    for r in rows:
        lines.append(fmt((r["title"], r["health"], r["ready"], r["version"], r["expected"])))
        if r.get("note"):
            lines.append(f"  → {r['note']}")
    lines.append("")
    lines.append(f"Ожидаемая версия: {expected or 'VERSION не найден'}")
    lines.append("Итог: PASS" if ok else "Итог: FAIL")
    return "\n".join(lines)


def main() -> int:
    ok, rows, expected = check_once()
    print(format_table(rows, expected=expected, ok=ok))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
