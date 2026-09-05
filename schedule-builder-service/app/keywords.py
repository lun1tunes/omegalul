"""tNavigator 22.2 SCHEDULE keyword catalogue (METRIC). Emit FRACTURE_SPECS, not FRACTURE_WELL."""

from __future__ import annotations

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
    "DATES": "Границы расчётных периодов",
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


INTENT_ALIASES = {
    "дат": ["DATES", "WCONPROD"],
    "ввод": ["DATES", "WCONPROD"],
    "commission": ["DATES", "WCONPROD"],
    "групп": ["GRUPTREE", "GCONPROD", "WELSPECS"],
    "перепривяз": ["GRUPTREE", "GCONPROD", "WELSPECS"],
    "gruptree": ["GRUPTREE"],
    "gconprod": ["GCONPROD"],
    "дебит": ["WCONPROD", "WCONINJE", "WELTARG"],
    "orat": ["WCONPROD"],
    "перфор": ["COMPDATMD"],
    "грп": ["FRACTURE_SPECS", "FRACTURE_STAGE", "WFRACP"],
    "fracture": ["FRACTURE_SPECS", "FRACTURE_STAGE"],
}


def search_keywords(intent: str) -> list[dict[str, Any]]:
    q = (intent or "").strip().lower()
    if not q:
        return all_keywords()
    wanted: list[str] = []
    for token, names in INTENT_ALIASES.items():
        if token in q:
            wanted.extend(names)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in wanted:
        item = keyword_object(name)
        if item and item["keyword"] not in seen:
            seen.add(item["keyword"])
            out.append(item)
    for item in all_keywords():
        blob = " ".join(
            [
                item["keyword"],
                item.get("description") or "",
                " ".join(f["name"] for f in item.get("fields") or []),
            ]
        ).lower()
        if q in blob and item["keyword"] not in seen:
            seen.add(item["keyword"])
            out.append(item)
    return out


def within_date_rank(keyword: str, orig_index: int = 0) -> float:
    name = normalize_keyword(keyword)
    if name == "DATES":
        return -1
    try:
        return float(WITHIN_DATE_KEYWORD_ORDER.index(name))
    except ValueError:
        apply_idx = WITHIN_DATE_KEYWORD_ORDER.index("APPLYSCRIPT")
        return apply_idx + 0.001 * orig_index
