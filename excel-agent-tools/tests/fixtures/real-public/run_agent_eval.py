#!/usr/bin/env python3
"""POST the real-public query pack at the live Excel Extractor webhook."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

PACK = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"


def load_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----omegalulAgentEval"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    for name, (filename, content, content_type) in files.items():
        chunks.append(
            (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                f"filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n"
            ).encode()
        )
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def post_extract(url: str, webhook_key: str, workbook: Path, prompt: str, timeout: int) -> dict:
    body, content_type = multipart(
        {"request": json.dumps({"prompt": prompt}, ensure_ascii=False)},
        {
            "file": (
                workbook.name,
                workbook.read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": UA,
            "Content-Type": content_type,
            "X-Excel-Webhook-Key": webhook_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        status = error.code
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"raw": raw[:4000]}
    payload["_http_status"] = status
    return payload


def post_continue(url: str, webhook_key: str, session_id: str, token: str, answer: str, timeout: int) -> dict:
    body, content_type = multipart(
        {
            "session_id": session_id,
            "clarification_response": json.dumps(
                {
                    "token": token,
                    "answers": [{"question_id": "table_selection", "answer": answer}],
                },
                ensure_ascii=False,
            ),
        },
        {},
    )
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": UA,
            "Content-Type": content_type,
            "X-Excel-Webhook-Key": webhook_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", "replace")
        status = error.code
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"raw": raw[:4000]}
    payload["_http_status"] = status
    return payload


def compact(result: dict) -> dict:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    records = data.get("records") if isinstance(data.get("records"), list) else []
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    return {
        "http_status": result.get("_http_status"),
        "status": result.get("status"),
        "message": result.get("message"),
        "next_action": result.get("next_action"),
        "columns": data.get("columns"),
        "row_count": data.get("row_count"),
        "stored_rows": data.get("stored_rows"),
        "returned_count": data.get("returned_count"),
        "truncated": data.get("truncated"),
        "sample_records": records[:3],
        "filters_applied": result.get("filters_applied"),
        "field_mapping": result.get("field_mapping"),
        "warnings": result.get("warnings"),
        "errors": result.get("errors"),
        "clarification": result.get("clarification"),
        "assumptions": result.get("assumptions"),
        "provenance": data.get("provenance"),
        "meta": meta,
    }


def _column_has_hint(column: str, hint: str) -> bool:
    folded_column = column.lower()
    folded_hint = hint.lower()
    index = folded_column.find(folded_hint)
    if index < 0:
        return False
    if index > 0 and folded_column[index - 1].isalnum():
        return False
    return True


def judge(query: dict, compact_result: dict) -> dict:
    expect = query.get("expect") or {}
    status = compact_result.get("status")
    allowed = expect.get("status_in") or []
    ok = status in allowed if allowed else status in {"success", "partial", "clarification_needed"}
    reasons = []
    if allowed and status not in allowed:
        reasons.append(f"status {status!r} not in {allowed}")
    columns_list = [str(column) for column in (compact_result.get("columns") or [])]
    columns = " ".join(columns_list)
    for hint in expect.get("column_hints") or []:
        if not any(_column_has_hint(column, hint) for column in columns_list) and hint.lower() not in columns.lower():
            reasons.append(f"missing column hint {hint!r}")
            ok = False
    for hint in expect.get("forbidden_column_hints") or []:
        if any(_column_has_hint(column, hint) for column in columns_list):
            reasons.append(f"unexpected column hint {hint!r}")
            ok = False
    allowed_substrings = expect.get("allowed_column_substrings") or []
    if allowed_substrings:
        for column in columns_list:
            if column.casefold().startswith("column "):
                continue
            if not any(_column_has_hint(column, hint) for hint in allowed_substrings):
                reasons.append(f"extra column {column!r}")
                ok = False
    provenance = compact_result.get("provenance") or []
    sheets = " ".join(str(item.get("sheet") or "") for item in provenance if isinstance(item, dict))
    for hint in expect.get("sheet_hints") or []:
        if hint.lower() not in sheets.lower():
            reasons.append(f"missing sheet hint {hint!r}")
            ok = False
    for sheet in expect.get("must_not_use_sheet") or []:
        if sheet.lower() in sheets.lower():
            reasons.append(f"used forbidden sheet {sheet!r}")
            ok = False
    stored = compact_result.get("stored_rows")
    if stored is None:
        stored = compact_result.get("returned_count")
    if "max_stored_rows" in expect and isinstance(stored, int) and stored > expect["max_stored_rows"]:
        reasons.append(f"stored_rows {stored} exceeds {expect['max_stored_rows']}")
        ok = False
    if "min_stored_rows" in expect and isinstance(stored, int) and stored < expect["min_stored_rows"]:
        reasons.append(f"stored_rows {stored} below {expect['min_stored_rows']}")
        ok = False
    return {"ok": ok and not reasons, "reasons": reasons}


def main() -> None:
    pack = json.loads((PACK / "queries.json").read_text(encoding="utf-8"))
    files_root = Path(os.environ.get("REAL_PUBLIC_XLSX_DIR", "/tmp/real-xlsx"))
    env = load_env(Path(os.environ.get("EXCEL_RUNTIME_ENV", "/tmp/real-xlsx/runtime.env")))
    webhook_url = os.environ.get("EXCEL_WEBHOOK_URL", "http://127.0.0.1:15678/webhook/excel-extract")
    timeout = int(os.environ.get("EXCEL_AGENT_TIMEOUT", "180"))
    workbooks = {item["id"]: item["file"] for item in pack["workbooks"]}
    results = []
    for query in pack["queries"]:
        workbook = files_root / workbooks[query["workbook"]]
        started = time.time()
        print(f"RUN {query['id']} file={workbook.name}", flush=True)
        payload = post_extract(webhook_url, env["excel_webhook_api_key"], workbook, query["prompt"], timeout)
        summary = compact(payload)
        continue_with = query.get("continue_with")
        if (
            continue_with
            and summary.get("status") == "clarification_needed"
            and summary.get("clarification")
            and summary.get("meta", {}).get("session_id")
        ):
            print(f"  CONTINUE {query['id']} -> {continue_with}", flush=True)
            payload = post_continue(
                webhook_url,
                env["excel_webhook_api_key"],
                summary["meta"]["session_id"],
                summary["clarification"]["token"],
                continue_with,
                timeout,
            )
            summary = compact(payload)
            summary["continued"] = True
            summary["continue_with"] = continue_with
        verdict = judge(query, summary)
        elapsed = round(time.time() - started, 1)
        row = {"id": query["id"], "elapsed_s": elapsed, "verdict": verdict, "result": summary}
        results.append(row)
        print(
            f"  {summary.get('status')} http={summary.get('http_status')} "
            f"rows={summary.get('returned_count')} ok={verdict['ok']} {elapsed}s",
            flush=True,
        )
        if verdict["reasons"]:
            print("  ", "; ".join(verdict["reasons"]), flush=True)
    out = {
        "webhook": webhook_url,
        "passed": sum(1 for row in results if row["verdict"]["ok"]),
        "failed": sum(1 for row in results if not row["verdict"]["ok"]),
        "results": results,
    }
    dest = Path(os.environ.get("REAL_PUBLIC_RESULTS", "/tmp/real-xlsx/agent-eval-results.json"))
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": out["passed"], "failed": out["failed"], "out": str(dest)}))


if __name__ == "__main__":
    main()
