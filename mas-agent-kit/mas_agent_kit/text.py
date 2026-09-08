"""Text for the engineer: the one ``human_text_problems`` gate and small Russian prose helpers.

Every question, option label, ``status_message`` and summary shown to a human goes through
``human_text_problems``. It is a *gate* (rejects machine-looking text), never a router.
"""

from __future__ import annotations

import json
import re
from typing import Any

# snake_case ids, key=value, JSON braces / brackets, a|b enums.
MACHINE_TOKEN_RE = re.compile(r"[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}\[\]<>]|\w\|\w")
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]{3,}")


def human_text_problems(text: Any, *, min_length: int = 12) -> list[str]:
    """Why a string is not fit for an engineer. Empty list = fine.

    Cyrillic prose with well names (1601, H_304R — uppercase) and keywords (WCONPROD) passes.
    """
    problems: list[str] = []
    stripped = str(text or "").strip()
    if len(stripped) < min_length:
        problems.append("слишком коротко для вопроса инженеру")
    if not _CYRILLIC_RE.search(stripped):
        problems.append("вопрос должен быть по-русски")
    tokens = sorted({m.group(0) for m in MACHINE_TOKEN_RE.finditer(stripped)})
    if tokens:
        problems.append("машинные токены: " + ", ".join(tokens[:6]))
    return problems


def option_problems(options: list[dict[str, str]]) -> list[str]:
    """Option labels are short by nature — only the machine-token / language checks apply."""
    out: list[str] = []
    for opt in options:
        for problem in human_text_problems(opt.get("label", "")):
            if "коротко" in problem or "по-русски" in problem:
                continue
            out.append(f"вариант «{opt.get('label', '')}»: {problem}")
    return out


def parse_jsonish(value: Any) -> Any:
    """``dict``/``list`` as-is; a JSON string → parsed value; anything else → ``None``."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip()[:1] in "{[":
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def options_for_human(raw: Any, *, limit: int = 8) -> list[dict[str, str]]:
    """Normalise LLM-supplied options: JSON list, ``[{value,label,hint}]`` or ``"a; b; c"`` → ``[{value,label}]``."""
    items = raw
    if isinstance(raw, str):
        parsed = parse_jsonish(raw)
        items = parsed if isinstance(parsed, list) else [part.strip() for part in raw.split(";") if part.strip()]
    if not isinstance(items, list):
        return []
    out: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("text") or item.get("value") or "").strip()
            value = str(item.get("value") or label).strip()
            hint = str(item.get("hint") or "").strip()
        else:
            label = str(item).strip()
            value = label
            hint = ""
        if not label:
            continue
        row = {"value": value, "label": label}
        if hint:
            row["hint"] = hint
        out.append(row)
    return out[:limit]


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Russian plural with the number: ``plural_ru(3, "скважина", "скважины", "скважин")`` → ``"3 скважины"``."""
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return f"{n} {one}"
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def list_preview(items: list[Any], limit: int = 6) -> str:
    """``"a, b, c и ещё 4"`` — short enumerations for summaries."""
    shown = ", ".join(str(item) for item in items[:limit])
    if len(items) > limit:
        shown += f" и ещё {len(items) - limit}"
    return shown


def slug(text: Any, default: str = "engineer") -> str:
    """Latin identifier for question ids / topics (``"Target group!"`` → ``"target_group"``)."""
    cleaned = re.sub(r"[^a-z0-9_]+", "_", str(text or "").strip().lower()).strip("_")
    return cleaned or default
