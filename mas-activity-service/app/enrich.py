"""Turn enrichment for MAS activity UI — brief, absolute time, duration, safe chips."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

# Tyumen shares Asia/Yekaterinburg (UTC+5, no DST).
TYUMEN_TZ = ZoneInfo("Asia/Yekaterinburg")

MAX_BRIEF_CHARS = 800
MAX_SUMMARY_CHARS = 500
SAFE_CHIP_KEYS = (
    "attempt",
    "fact_count",
    "gap_count",
    "conflict_count",
    "warning_count",
    "release_ready",
    "fields",
    "source_snapshot_hash",
    "correlation_id",
    "dropped_gap_count",
    "action",
    "requested_by",
    "gate_id",
    "gate_kind",
    "file_count",
    "backend",
)
SECRETISH = re.compile(r"(prompt|secret|token|password|authorization|api[_-]?key|binary|content|session)", re.I)

BRIEF_TEMPLATES: dict[str, str] = {
    "DELEGATED": (
        "Оркестратор выбрал следующего специалиста и передал ему ограниченный пакет задачи. "
        "Дальше работа идёт внутри этого specialist; оркестратор ждёт структурированный результат."
    ),
    "EXCEL_EVIDENCE_READY": (
        "Excel Extractor завершил извлечение и сформировал пакет фактов со snapshot/correlation. "
        "Пакет передаётся Schedule Builder для CREATE/REVISE без прямого доступа к workbook."
    ),
    "INVALID_SOURCE_FACTS_PACKET": (
        "Handoff в Schedule Builder заблокирован: в Excel-результате нет обязательных "
        "source_snapshot_hash и correlation_id. Нужен повторный governed extract, а не угадывание."
    ),
    "CALCULATION_DATA_READY": (
        "Calculation Specialist вернул геометрический результат. "
        "Оркестратор передаёт его дальше только как компактные данные для Schedule Builder."
    ),
    "SCHEDULE_EVIDENCE_GAP": (
        "Schedule Builder остановился на typed evidence_gap: не хватает конкретных полей. "
        "Оркестратор запускает узкий Excel-запрос только по этим полям, без полного переразбора книги."
    ),
    "MALFORMED_EVIDENCE_GAP": (
        "Цикл evidence остановлен: gap пришёл без обязательных полей "
        "(entity/effective_at/keyword/field/reason/expected_format). Бюджет Excel не тратится."
    ),
    "STALLED_EVIDENCE_LOOP": (
        "Тот же gap и тот же snapshot повторились — петля остановлена политикой. "
        "Нужен человек: новые факты, другой источник или смена scope."
    ),
    "EXCEL_EVIDENCE_BUDGET_EXHAUSTED": (
        "Исчерпан бюджет Excel-итераций в evidence loop. "
        "Дальше только HITL: дать факты вручную, сменить источник или отклонить задачу."
    ),
    "BUILDER_ITERATION_BUDGET_EXHAUSTED": (
        "Исчерпан бюджет итераций Schedule Builder. "
        "Автоматический Excel-retry больше не запускается до решения человека."
    ),
    "RESUME_SCHEDULE": (
        "Excel вернул недостающие факты с тем же correlation. "
        "Schedule Builder возобновляется на новой версии пакета, без повторной загрузки workbook."
    ),
    "INVALID_EXCEL_EVIDENCE_SNAPSHOT": (
        "Resume Builder запрещён: correlation/snapshot Excel-результата не совпали с ожидаемыми. "
        "Это fail-closed защита от подмены evidence mid-loop."
    ),
    "TASK_STARTED": (
        "Инженер создал новую задачу из Activity UI. "
        "Оркестратор принимает objective и вложения и начинает intake/planning."
    ),
    "AWAITING_HUMAN": (
        "Задача ждёт человека: нужны факты, решение или утверждение выпуска. "
        "Ответьте в чате — reply, approve или reject."
    ),
    "HUMAN_REPLY": (
        "Инженер отправил ответ в HITL-gate. "
        "Оркестратор применит ответ к текущей версии задачи и продолжит маршрут."
    ),
    "HUMAN_APPROVED": (
        "Инженер утвердил результат на HITL-gate. "
        "Задача продолжается как approved resume без повторного планирования с нуля."
    ),
    "HUMAN_REJECTED": (
        "Инженер отклонил результат на HITL-gate. "
        "Оркестратор фиксирует reject и не выпускает draft как approved."
    ),
}


def parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_abs(dt: datetime) -> str:
    local = dt.astimezone(TYUMEN_TZ) if dt.tzinfo else dt.replace(tzinfo=timezone.utc).astimezone(TYUMEN_TZ)
    return local.strftime("%Y-%m-%d %H:%M:%S") + " Тюмень"


def format_duration(ms: int | None) -> str | None:
    if ms is None:
        return None
    if ms < 0:
        ms = 0
    if ms < 1000:
        return f"{ms} ms"
    seconds = ms / 1000.0
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes = int(seconds // 60)
    rem = seconds - minutes * 60
    return f"{minutes}m {rem:04.1f}s"


def outcome_for(status: str | None) -> str:
    s = (status or "").upper()
    if s in {
        "SUCCEEDED",
        "EXCEL_EVIDENCE_READY",
        "CALCULATION_DATA_READY",
        "RESUME_SCHEDULE",
        "HUMAN_APPROVED",
        "COMPLETED",
        "TASK_STARTED",
    }:
        return "ok"
    if s == "DELEGATED":
        return "info"
    if s in {"SCHEDULE_EVIDENCE_GAP", "PARTIAL", "AWAITING_HUMAN", "HUMAN_REPLY", "NEEDS_INPUT", "NEEDS_DECISION", "NEEDS_APPROVAL"}:
        return "wait"
    if any(x in s for x in ("INVALID", "MALFORMED", "STALLED", "EXHAUSTED", "FATAL", "FAILED", "ERROR", "REJECT")):
        return "block"
    return "info"


def build_brief(*, status: str | None, summary: str | None, brief: str | None, details: dict[str, Any]) -> str:
    raw = (brief or "").strip()
    if not raw:
        raw = BRIEF_TEMPLATES.get((status or "").upper(), "").strip()
    if not raw:
        raw = (summary or "").strip()
    if not raw:
        raw = "Специалист завершил шаг; оркестратор фиксирует handoff в ленте активности."
    # Keep 1–4 short sentences visually: collapse whitespace, hard-cap chars.
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) > MAX_BRIEF_CHARS:
        raw = raw[: MAX_BRIEF_CHARS - 1].rstrip() + "…"
    # Light contextual second sentence from safe details when brief is a template one-liner.
    extras = []
    if isinstance(details.get("fact_count"), (int, float)):
        extras.append(f"Фактов в пакете: {int(details['fact_count'])}.")
    if isinstance(details.get("gap_count"), (int, float)):
        extras.append(f"Недостающих полей: {int(details['gap_count'])}.")
    if details.get("fields"):
        extras.append(f"Поля: {details['fields']}.")
    if extras and len(raw) < 280:
        joined = f"{raw} {' '.join(extras[:2])}"
        raw = joined if len(joined) <= MAX_BRIEF_CHARS else raw
    return raw


def safe_chips(details: dict[str, Any]) -> list[dict[str, Any]]:
    chips = []
    for key in SAFE_CHIP_KEYS:
        if key not in details:
            continue
        if SECRETISH.search(key):
            continue
        value = details[key]
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value[:8])
        if not isinstance(value, (str, int, float, bool)):
            continue
        text = str(value)
        if len(text) > 120:
            text = text[:119] + "…"
        chips.append({"id": key, "label": key, "value": text if not isinstance(value, bool) else value})
    return chips[:6]


def enrich_turn(raw: dict[str, Any], *, received_at: str | None = None) -> dict[str, Any]:
    data = dict(raw)
    handoff = data.get("handoff") if isinstance(data.get("handoff"), dict) else {}
    details = data.get("details") if isinstance(data.get("details"), dict) else {}
    if handoff and not details:
        details = handoff.get("details") if isinstance(handoff.get("details"), dict) else {}
    details = {k: v for k, v in details.items() if not SECRETISH.search(str(k))}

    status = data.get("status")
    summary = str(data.get("summary") or data.get("text") or "")[:MAX_SUMMARY_CHARS]
    brief = build_brief(status=status, summary=summary, brief=data.get("brief"), details=details)

    at_dt = parse_iso(data.get("at")) or parse_iso(received_at) or datetime.now(timezone.utc)
    duration_ms = data.get("duration_ms")
    if duration_ms is None and isinstance(details.get("duration_ms"), (int, float)):
        duration_ms = details.get("duration_ms")
    try:
        duration_ms = int(duration_ms) if duration_ms is not None else None
    except (TypeError, ValueError):
        duration_ms = None

    return {
        "turn_id": int(data.get("turn_id") or 0),
        "at": at_dt.isoformat(),
        "at_abs": format_abs(at_dt),
        "kind": data.get("event_type") or data.get("kind") or "handoff",
        "stage": data.get("stage"),
        "status": status,
        "outcome": outcome_for(status if isinstance(status, str) else None),
        "text": summary,
        "brief": brief,
        "duration_ms": duration_ms,
        "duration_label": format_duration(duration_ms),
        "from": {
            "specialist_id": data.get("from_specialist") or handoff.get("from_specialist"),
            "role": data.get("from_role") or handoff.get("from_role") or data.get("actor") or "Orchestrator",
        },
        "to": {
            "specialist_id": data.get("to_specialist") or handoff.get("to_specialist"),
            "role": data.get("to_role") or handoff.get("to_role") or "Specialist",
        },
        "details": details,
        "chips": safe_chips(details),
        "trace_id": data.get("trace_id"),
        "received_at": received_at or datetime.now(timezone.utc).isoformat(),
    }
