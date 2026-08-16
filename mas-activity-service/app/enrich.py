"""Turn enrichment for MAS activity UI — brief, absolute time, duration, safe chips."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

# Display clock: Asia/Yekaterinburg (UTC+5, no DST). Label as UTC offset, not city name.
DISPLAY_TZ = ZoneInfo("Asia/Yekaterinburg")
TYUMEN_TZ = DISPLAY_TZ  # backwards-compatible alias

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
CHIP_LABELS: dict[str, str] = {
    "attempt": "Попытка",
    "fact_count": "Фактов",
    "gap_count": "Пропусков",
    "conflict_count": "Конфликтов",
    "warning_count": "Предупреждений",
    "release_ready": "К выпуску",
    "fields": "Поля",
    "source_snapshot_hash": "Снимок",
    "correlation_id": "Связка",
    "dropped_gap_count": "Отброшено",
    "action": "Действие",
    "requested_by": "Запросил",
    "gate_id": "Шлюз",
    "gate_kind": "Тип шлюза",
    "file_count": "Файлов",
    "backend": "Бэкенд",
}
SECRETISH = re.compile(r"(prompt|secret|token|password|authorization|api[_-]?key|binary|content|session)", re.I)

# Presentation authority: one short RU sentence per known status.
BRIEF_TEMPLATES: dict[str, str] = {
    "DELEGATED": "Задачу передали специалисту — ждём результат.",
    "EXCEL_EVIDENCE_READY": "Из Excel собрали факты и передали в Schedule Builder.",
    "INVALID_SOURCE_FACTS_PACKET": "Пакет из Excel неполный — Builder не запускаем, нужен повторный разбор.",
    "CALCULATION_DATA_READY": "Расчёт готов — данные уходят в Schedule Builder.",
    "SCHEDULE_EVIDENCE_GAP": "В schedule не хватает полей — узкий запрос к Excel.",
    "MALFORMED_EVIDENCE_GAP": "Запрос к Excel остановлен: в пропуске нет обязательных полей.",
    "STALLED_EVIDENCE_LOOP": "Тот же пропуск повторился — нужен человек.",
    "EXCEL_EVIDENCE_BUDGET_EXHAUSTED": "Лимит обращений к Excel исчерпан — нужен человек.",
    "BUILDER_ITERATION_BUDGET_EXHAUSTED": "Лимит итераций Builder исчерпан — нужен человек.",
    "RESUME_SCHEDULE": "Недостающие факты получены — продолжаем сборку schedule.",
    "INVALID_EXCEL_EVIDENCE_SNAPSHOT": "Снимок Excel не совпал — продолжение запрещено.",
    "TASK_STARTED": "Задача создана — оркестратор принимает ввод и вложения.",
    "AWAITING_HUMAN": "Ждём ваш ответ: факты, решение или утверждение.",
    "HUMAN_REPLY": "Ответ принят — продолжаем задачу.",
    "HUMAN_APPROVED": "Выпуск утверждён — задача продолжается.",
    "HUMAN_REJECTED": "Результат отклонён — выпуск не делаем.",
    "SCHEDULE_DRAFT_READY": "Черновик schedule готов.",
    "VERIFIED": "Проверка пройдена.",
    "SUCCEEDED": "Шаг выполнен успешно.",
    "COMPLETED": "Задача завершена.",
    "NEEDS_INPUT": "Нужны дополнительные данные от вас.",
    "NEEDS_DECISION": "Нужно ваше решение, чтобы продолжить.",
    "NEEDS_APPROVAL": "Нужно ваше утверждение выпуска.",
    "ORCH_CONFLICT": "Оркестратор отклонил запрос — смотрите причину в ленте.",
    "CONFLICT": "Конфликт состояния — обновите статус и повторите с актуальной версией.",
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
    local = dt.astimezone(DISPLAY_TZ) if dt.tzinfo else dt.replace(tzinfo=timezone.utc).astimezone(DISPLAY_TZ)
    offset = local.utcoffset()
    total_minutes = int((offset.total_seconds() if offset else 0) // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    utc_label = f"UTC{sign}{hours}" if minutes == 0 else f"UTC{sign}{hours:02d}:{minutes:02d}"
    return local.strftime("%Y-%m-%d %H:%M:%S") + f" {utc_label}"


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
        "SCHEDULE_DRAFT_READY",
        "VERIFIED",
    }:
        return "ok"
    if s == "DELEGATED":
        return "info"
    if s in {
        "SCHEDULE_EVIDENCE_GAP",
        "PARTIAL",
        "AWAITING_HUMAN",
        "HUMAN_REPLY",
        "NEEDS_INPUT",
        "NEEDS_DECISION",
        "NEEDS_APPROVAL",
    }:
        return "wait"
    if any(x in s for x in ("INVALID", "MALFORMED", "STALLED", "EXHAUSTED", "FATAL", "FAILED", "ERROR", "REJECT")):
        return "block"
    return "info"


def build_brief(*, status: str | None, summary: str | None, brief: str | None, details: dict[str, Any]) -> str:
    """Known status → laconic RU template wins over producer brief/summary (presentation layer)."""
    status_key = (status or "").upper()
    raw = BRIEF_TEMPLATES.get(status_key, "").strip()
    if not raw:
        raw = (brief or "").strip()
    if not raw:
        raw = (summary or "").strip()
    if not raw:
        raw = "Шаг зафиксирован в ленте активности."
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) > MAX_BRIEF_CHARS:
        raw = raw[: MAX_BRIEF_CHARS - 1].rstrip() + "…"
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
        chips.append(
            {
                "id": key,
                "label": CHIP_LABELS.get(key, key),
                "value": text if not isinstance(value, bool) else value,
            }
        )
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
            "role": data.get("to_role") or handoff.get("to_role") or "User",
        },
        "details": details,
        "chips": safe_chips(details),
        "trace_id": data.get("trace_id"),
        "received_at": received_at or datetime.now(timezone.utc).isoformat(),
    }
