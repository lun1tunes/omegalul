"""Single source for MAS knowledge spaces, selectors, and the SCHEDULE allowlist.

``schedule_rag_workflows.KEYWORDS`` re-exports ``KEYWORDS``. Schedule Builder
``app/keywords.py`` keeps a field copy; pytest asserts equality.
"""
from __future__ import annotations

import json

KEYWORDS = [
    "DATES", "INCLUDE", "GRUPTREE", "WELSPECS", "WELLTRACK", "COMPDATMD",
    "WCONHIST", "WCONPROD", "WCONINJE", "GCONPROD", "GCONINJE", "GUIDERAT", "GSATPROD", "GSATINJE", "WELLSTRE", "WINJGAS", "GINJGAS", "BRANPROP", "NODEPROP", "GNETDP", "NETBALAN",
    "FRACTURE_TEMPLATE", "FRACTURE_SPECS", "FRACTURE_STAGE", "WECON", "WTEST",
    "WELTARG", "WNETDP", "WPIMULT", "WDFAC", "WEFAC", "WELOPEN", "WELDRAW", "WLIST", "WFRACP", "WFRACPL",
    "VFPPROD", "WVFPDP", "ACTIONX", "DELAYACT", "ENDACTIO", "UDQ", "UDT", "APPLYSCRIPT",
]

NAMESPACES = {
    "schedule_mvp": {
        "types": ["keyword_instruction", "worked_example"],
        "keywordMode": "schedule",
        "requireCoverage": True,
        "requireSchema": True,
        "section": "SCHEDULE",
    },
    "excel_protocol": {
        "types": ["protocol_instruction"],
        "keywordMode": "open",
        "requireCoverage": True,
        "requireSchema": False,
        "section": "EXCEL",
    },
    "orchestrator_routing": {
        "types": ["routing_card"],
        "keywordMode": "open",
        "requireCoverage": False,
        "requireSchema": False,
        "section": "ORCHESTRATOR",
    },
    "specialist_template": {
        "types": ["capability_instruction", "worked_example"],
        "keywordMode": "open",
        "requireCoverage": True,
        "requireSchema": False,
        "section": "SPECIALIST",
    },
}

COVERAGE_TYPES = ("keyword_instruction", "protocol_instruction", "capability_instruction")

SELECTORS: dict[str, dict] = {
    "orchestrator": {
        "target_base": "orchestrator_routing",
        "knowledge_types": ["routing_card"],
        "access_scope": "petroleum-engineering",
        "top_k": 20,
        "topics": [],
        "max_cards": 6,
        "text_limit": 1500,
    },
    "excel": {
        "target_base": "excel_protocol",
        "knowledge_types": ["protocol_instruction"],
        "access_scope": "petroleum-engineering",
        "top_k": 8,
        "topics": ["протокол"],
        "max_cards": 8,
        "text_limit": 1200,
        "on_demand_text_limit": 4000,
    },
    "schedule": {
        "target_base": "schedule_mvp",
        "knowledge_types": ["keyword_instruction", "worked_example"],
        "access_scope": "petroleum-engineering",
        "top_k": 10,
        "topics": [],
        "max_cards": 6,
        "text_limit": 800,
        "on_demand_text_limit": 4000,
    },
    "specialist": {
        "target_base": "specialist_template",
        "knowledge_types": ["capability_instruction", "worked_example"],
        "access_scope": "petroleum-engineering",
        "top_k": 8,
        "topics": ["specialist", "capability"],
        "max_cards": 8,
        "text_limit": 1200,
    },
}


def namespaces_js() -> str:
    ns = json.dumps(NAMESPACES, ensure_ascii=False, separators=(",", ":"))
    types = json.dumps(list(COVERAGE_TYPES), ensure_ascii=False, separators=(",", ":"))
    return (
        f"const NAMESPACES={ns};\n"
        f"const COVERAGE_TYPES=new Set({types});\n"
        "const ALLOWED_BASES=new Set(Object.keys(NAMESPACES));"
    )
