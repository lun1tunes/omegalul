"""Deterministic within-DATES keyword emit order for SCHEDULE text assembly.

Stage-3 renderer / timeline emit must place keywords in this order inside a
single DATES clock block. This is algorithm policy, not RAG instruction text.

Notes:
- DATES opens a clock segment and is never ranked inside the segment.
- WELLTRACK is inserted before COMPDATMD (MD completions require trajectory).
- WELLSTREE (legacy/typo) maps to allowlisted WELLSTRE.
- Keywords outside this list keep relative IR order between CORE and APPLYSCRIPT.
"""
from __future__ import annotations

import json

# Expert CREATE order inside one DATES block (APPLYSCRIPT always last among ranked).
WITHIN_DATE_KEYWORD_ORDER: list[str] = [
    "WELSPECS",
    "INCLUDE",
    "LGROFF",
    "LGRONN",
    "WELLTRACK",  # COMPDATMD prerequisite; inject even if omitted from expert lists
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
    "WELLSTRE",  # emit WELLSTRE (WELLSTREE is alias)
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
    # Commissioning / control keywords (common in REVISE emit; before WGRUPCON)
    "WCONHIST",
    "WCONPROD",
    "WELOPEN",
    "WEFAC",
    "WGRUPCON",
    "APPLYSCRIPT",
]

# Synonyms → emit name used in ranking.
WITHIN_DATE_KEYWORD_ALIASES: dict[str, str] = {
    "WELLSTREE": "WELLSTRE",
    "FRACTURE_WELL": "FRACTURE_SPECS",
    "WELLTARG": "WELTARG",
}


def within_date_order_js() -> str:
    """JS helpers: WITHIN_DATE_KEYWORD_ORDER + rankWithinDateKeyword(kw, origIndex)."""
    order = json.dumps(WITHIN_DATE_KEYWORD_ORDER, ensure_ascii=False)
    aliases = json.dumps(WITHIN_DATE_KEYWORD_ALIASES, ensure_ascii=False)
    return f"""
const WITHIN_DATE_KEYWORD_ORDER={order};
const WITHIN_DATE_KEYWORD_ALIASES={aliases};
const normalizeWithinDateKeyword=kw=>{{
  const raw=String(kw||'').trim().toUpperCase();
  return WITHIN_DATE_KEYWORD_ALIASES[raw]||raw;
}};
const withinDateKeywordRank=(kw,origIndex=0)=>{{
  const name=normalizeWithinDateKeyword(kw);
  if(name==='DATES')return -1;
  const idx=WITHIN_DATE_KEYWORD_ORDER.indexOf(name);
  if(idx>=0)return idx;
  // Unlisted keywords keep relative order between CORE and APPLYSCRIPT.
  const applyIdx=WITHIN_DATE_KEYWORD_ORDER.indexOf('APPLYSCRIPT');
  const base=applyIdx>=0?applyIdx:WITHIN_DATE_KEYWORD_ORDER.length;
  return base+0.001*(Number(origIndex)||0);
}};
const compareWithinDateKeywords=(a,b,ai=0,bi=0)=>{{
  const ra=withinDateKeywordRank(a,ai),rb=withinDateKeywordRank(b,bi);
  if(ra!==rb)return ra-rb;
  return (Number(ai)||0)-(Number(bi)||0);
}};
""".strip()
