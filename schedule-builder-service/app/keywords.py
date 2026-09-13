"""tNavigator 22.2 SCHEDULE keyword catalogue (METRIC). Emit FRACTURE_SPECS, not FRACTURE_WELL."""

from __future__ import annotations

import re
from typing import Any

KEYWORDS: list[str] = [
    "DATES",
    "INCLUDE",
    "GRUPTREE",
    "WELSPECS",
    "WELLTRACK",
    "COMPDATMD",
    "WCONHIST",
    "WCONPROD",
    "WCONINJE",
    "GCONPROD",
    "GCONINJE",
    "GUIDERAT",
    "GSATPROD",
    "GSATINJE",
    "WELLSTRE",
    "WINJGAS",
    "GINJGAS",
    "BRANPROP",
    "NODEPROP",
    "GNETDP",
    "NETBALAN",
    "FRACTURE_TEMPLATE",
    "FRACTURE_SPECS",
    "FRACTURE_STAGE",
    "WECON",
    "WTEST",
    "WELTARG",
    "WNETDP",
    "WPIMULT",
    "WDFAC",
    "WEFAC",
    "WELOPEN",
    "WELDRAW",
    "WLIST",
    "WFRACP",
    "WFRACPL",
    "VFPPROD",
    "WVFPDP",
    "ACTIONX",
    "DELAYACT",
    "ENDACTIO",
    "UDQ",
    "UDT",
    "APPLYSCRIPT",
]

ALIASES = {
    "WELLSTREE": "WELLSTRE",
    "FRACTURE_WELL": "FRACTURE_SPECS",
    "WELLTARG": "WELTARG",
}

WITHIN_DATE_KEYWORD_ORDER: list[str] = [
    "WELSPECS",
    "INCLUDE",
    "LGROFF",
    "LGRONN",
    "WELLTRACK",
    "COMPDATMD",
    "WLIST",
    "GRUPTREE",
    "BRANPROP",
    "NODEPROP",
    "WFRACPL",
    "FRACTURE_SPECS",
    "FRACTURE_STAGE",
    "FRACTURE_PIMULT",
    "WFRACP",
    "WELLSTRE",
    "UDQ",
    "GSATCOMP",
    "GSATPROD",
    "WINJGAS",
    "GINJGAS",
    "GCONINJE",
    "WCONINJE",
    "WTRACER",
    "GCONPROD",
    "WECON",
    "WTEST",
    "WELTARG",
    "WCONHIST",
    "WCONPROD",
    "WELOPEN",
    "WEFAC",
    "WGRUPCON",
    "APPLYSCRIPT",
]

# Table-style keywords: records end with `/`, then a bare block `/` and a blank line.
TABLE_KEYWORDS = {
    "INCLUDE",
    "WCONPROD",
    "WCONINJE",
    "WCONHIST",
    "WELSPECS",
    "COMPDATMD",
    "GRUPTREE",
    "WELOPEN",
    "WEFAC",
    "WELTARG",
    "WECON",
    "WTEST",
    "WPIMULT",
    "GCONPROD",
    "GCONINJE",
    "DATES",
    "FRACTURE_SPECS",
    "FRACTURE_STAGE",
    "WELLTRACK",
}

FIELDS: dict[str, list[dict[str, Any]]] = {
    "DATES": [{"name": "date", "type": "date", "required": True, "unit": None, "description": "Clock date"}],
    "WCONPROD": [
        {"name": "well", "type": "string", "required": True, "unit": None, "description": "Имя скважины"},
        {"name": "status", "type": "string", "required": False, "unit": None, "description": "OPEN/SHUT/..."},
        {"name": "ORAT", "type": "number", "required": False, "unit": "m3/d", "description": "Oil rate"},
        {"name": "WRAT", "type": "number", "required": False, "unit": "m3/d", "description": "Water rate"},
        {"name": "GRAT", "type": "number", "required": False, "unit": "sm3/d", "description": "Gas rate"},
        {"name": "LRAT", "type": "number", "required": False, "unit": "m3/d", "description": "Liquid rate"},
        {"name": "RESV", "type": "number", "required": False, "unit": None, "description": "Reservoir volume rate"},
        {"name": "BHP", "type": "number", "required": False, "unit": "bar", "description": "Bottomhole pressure"},
        {"name": "THP", "type": "number", "required": False, "unit": "bar", "description": "Tubing head pressure"},
    ],
    "WCONINJE": [
        {"name": "well", "type": "string", "required": True, "unit": None, "description": "Имя скважины"},
        {"name": "type", "type": "string", "required": False, "unit": None, "description": "WATER/GAS"},
        {"name": "status", "type": "string", "required": False, "unit": None, "description": "OPEN/SHUT"},
        {"name": "RATE", "type": "number", "required": False, "unit": "m3/d", "description": "Injection rate"},
        {"name": "BHP", "type": "number", "required": False, "unit": "bar", "description": "BHP"},
    ],
    "WELSPECS": [
        {"name": "well", "type": "string", "required": True, "unit": None, "description": "Имя скважины"},
        {"name": "group", "type": "string", "required": False, "unit": None, "description": "Группа"},
        {"name": "i", "type": "integer", "required": False, "unit": None, "description": "I"},
        {"name": "j", "type": "integer", "required": False, "unit": None, "description": "J"},
        {"name": "ref_depth", "type": "number", "required": False, "unit": "m", "description": "Ref depth"},
        {"name": "phase", "type": "string", "required": False, "unit": None, "description": "OIL/WATER/GAS"},
    ],
    "WELOPEN": [
        {"name": "well", "type": "string", "required": True, "unit": None, "description": "Имя скважины"},
        {"name": "status", "type": "string", "required": False, "unit": None, "description": "OPEN/SHUT"},
    ],
    "WEFAC": [
        {"name": "well", "type": "string", "required": True, "unit": None, "description": "Имя скважины"},
        {"name": "factor", "type": "number", "required": False, "unit": None, "description": "Efficiency factor"},
    ],
    "COMPDATMD": [
        {"name": "well", "type": "string", "required": True, "unit": None, "description": "Имя скважины"},
        {"name": "md1", "type": "number", "required": False, "unit": "m", "description": "Top MD"},
        {"name": "md2", "type": "number", "required": False, "unit": "m", "description": "Bottom MD"},
        {"name": "status", "type": "string", "required": False, "unit": None, "description": "OPEN/SHUT"},
    ],
    "GRUPTREE": [
        {"name": "child", "type": "string", "required": True, "unit": None, "description": "Child group/well"},
        {"name": "parent", "type": "string", "required": True, "unit": None, "description": "Parent group"},
    ],
    "WELTARG": [
        {"name": "well", "type": "string", "required": True, "unit": None, "description": "Имя скважины"},
        {"name": "quantity", "type": "string", "required": True, "unit": None, "description": "ORAT/WRAT/GRAT/LRAT/RESV/BHP/THP/TARGTHP/VFP/LIFT/GUID/DEPTH"},
        {"name": "value", "type": "number", "required": True, "unit": None, "description": "Новое значение"},
    ],
    "WCONHIST": [
        {"name": "well", "type": "string", "required": True, "unit": None, "description": "Имя исторической скважины"},
        {"name": "status", "type": "string", "required": False, "unit": None, "description": "OPEN/SHUT"},
    ],
    "WECON": [
        {"name": "well", "type": "string", "required": True, "unit": None, "description": "Имя скважины"},
        {"name": "min_orat", "type": "number", "required": False, "unit": "m3/d", "description": "Минимальный экономический ORAT"},
        {"name": "max_wct", "type": "number", "required": False, "unit": None, "description": "Максимальная обводнённость"},
        {"name": "action", "type": "string", "required": False, "unit": None, "description": "NONE/CON/WELL"},
    ],
    "WTEST": [
        {"name": "well", "type": "string", "required": True, "unit": None, "description": "Имя скважины"},
        {"name": "interval", "type": "number", "required": True, "unit": "day", "description": "Интервал проверки"},
        {"name": "reason", "type": "string", "required": True, "unit": None, "description": "P/E/G"},
    ],
    "WPIMULT": [
        {"name": "well", "type": "string", "required": True, "unit": None, "description": "Имя скважины"},
        {"name": "mult", "type": "number", "required": True, "unit": None, "description": "Множитель CF"},
    ],
    "GCONPROD": [
        {"name": "group", "type": "string", "required": True, "unit": None, "description": "Имя группы или FIELD"},
        {"name": "control", "type": "string", "required": True, "unit": None, "description": "ORAT/WRAT/GRAT/LRAT/RESV/FLD"},
    ],
}

METHODS = ["create_record", "update_field", "validate_record"]

DESCRIPTIONS = {
    "DATES": "Даты расчётных периодов (шаги календаря SCHEDULE)",
    "WCONPROD": "Управление добывающей скважиной",
    "WCONINJE": "Управление нагнетательной скважиной",
    "WELSPECS": "Спецификация скважины",
    "WELOPEN": "Открытие/закрытие скважины",
    "WEFAC": "Коэффициент эксплуатации",
    "COMPDATMD": "Перфорация по MD",
    "GRUPTREE": "Иерархия групп",
    "WELTARG": "Целевое управление скважиной",
    "WCONHIST": "Исторический контроль добывающей скважины; не прогноз",
    "WECON": "Экономические пределы скважины; не целевой дебит",
    "WTEST": "Политика проверки и переоткрытия закрытой скважины",
    "WPIMULT": "Множитель проводимости перфорации",
    "GCONPROD": "Прогнозный контроль группы или FIELD; не well control",
    "FRACTURE_SPECS": "Параметры ГРП скважины (legacy name; layout FRACTURE_WELL §12.2.131)",
}


def normalize_keyword(name: str) -> str:
    raw = str(name or "").strip().upper()
    return ALIASES.get(raw, raw)


def keyword_object(name: str) -> dict[str, Any] | None:
    code = normalize_keyword(name)
    if code not in KEYWORDS:
        return None
    from .schema_keyword import details_for

    details = details_for(code)
    variants = list((details.variants if details else []) or [])
    primary = variants[0] if variants else {}
    fields = list(primary.get("parameters") or FIELDS.get(code) or [{"name": "tokens", "type": "string", "required": False, "unit": None, "description": "Raw record tokens"}])
    return {
        "keyword": code,
        "section": "SCHEDULE",
        "description": DESCRIPTIONS.get(code, f"SCHEDULE keyword {code}"),
        "fields": fields,
        "details": details.model_dump() if details else {"kind": "schedule_keyword", "keyword": code, "variants": []},
        "methods": [
            {"name": method, "description": method, "input_schema": {"type": "object"}}
            for method in METHODS
        ],
        "examples": [],
        "constraints": {"units": "METRIC", "simulator": "tNavigator 22.2"},
        "source": details.source if details else "builtin",
    }


def all_keywords() -> list[dict[str, Any]]:
    return [keyword_object(name) for name in KEYWORDS]


# Lexical catalogue search only — no stem→keyword maps (A6).
_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё_]{2,}")


def _catalogue_blob(item: dict[str, Any]) -> str:
    parts = [str(item.get("keyword") or ""), str(item.get("description") or "")]
    for field in item.get("fields") or []:
        if not isinstance(field, dict):
            continue
        parts.append(str(field.get("name") or ""))
        parts.append(str(field.get("description") or ""))
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    for variant in details.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        for param in variant.get("parameters") or []:
            if not isinstance(param, dict):
                continue
            parts.append(str(param.get("name") or ""))
            parts.append(str(param.get("description") or ""))
    return " ".join(parts).lower()


def search_keywords(intent: str) -> list[dict[str, Any]]:
    """Rank catalogue keywords by overlap with the query text (name, description, schema fields).

    Does not map Russian stems onto a keyword list. Empty query → full catalogue.
    """
    q = (intent or "").strip().lower()
    catalog = [item for item in all_keywords() if item]
    if not q:
        return catalog
    tokens = [m.group(0).lower() for m in _WORD_RE.finditer(q)]
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in catalog:
        blob = _catalogue_blob(item)
        keyword = str(item.get("keyword") or "").lower()
        score = 0
        if keyword and (keyword == q or keyword in q.split() or q == keyword):
            score += 100
        if q and q in blob:
            score += 20
        blob_words = {m.group(0).lower() for m in _WORD_RE.finditer(blob)}
        for tok in tokens:
            if tok == keyword:
                score += 50
            elif tok in blob_words:
                score += 3
            elif tok in blob:
                score += 1
        if score:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("keyword") or "")))
    return [item for _score, item in scored]


def within_date_rank(keyword: str, orig_index: int = 0) -> float:
    name = normalize_keyword(keyword)
    if name == "DATES":
        return -1
    try:
        return float(WITHIN_DATE_KEYWORD_ORDER.index(name))
    except ValueError:
        apply_idx = WITHIN_DATE_KEYWORD_ORDER.index("APPLYSCRIPT")
        return apply_idx + 0.001 * orig_index
