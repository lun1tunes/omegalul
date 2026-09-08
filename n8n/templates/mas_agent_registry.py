"""Single source of the ``agent_registry`` table: columns, DDL and the seed rows.

Phase 2 (MAS_REFACTORING_PLAN.md §3 п.2): the registry is *executable*. A row says how the
orchestrator calls the agent (``invoke``), what it needs and gives (``input_required`` /
``output_provides``, ``input_schema`` / ``output_schema``), whether it may ask the engineer
(``hitl_policy``), whether it is enabled and which version it is. The orchestrator knows no agent
by name — it reads this table (``Load agent registry``) and RAG policy cards.

Consumers (all generated from here, never edited by hand):

- ``generate_mas_control_plane_proxy.py`` → ``schema`` / ``list_agents`` / ``upsert_agent`` SQL of
  ``MAS — Control Plane Proxy`` and the SQL files ``postgres-init/02-mas-control-plane.sql``,
  ``postgres-init/03-schedule-builder-registry.sql``, ``mas-activity-service/app/sql/control_plane.sql``.
- ``generate_mas_orchestrator.py`` → ``Load agent registry`` column list.

``invoke`` shapes (resolved deterministically by ``resolveInvoke`` in ``mas_state_utils.py``):

- ``{"kind": "n8n_workflow", "workflow_id": "<n8n workflow id>", "workflow_name": "…"}`` — the agent is
  an n8n sub-workflow (``executeWorkflowTrigger``) called with ``{agent_task}``. Lab: CLI import keeps
  the ids below. Field: after UI import the id is new — set it via ``MAS — Runtime Config`` →
  ``agent_workflow_ids`` (JSON ``{"<agent_id>": "<id>"}``) or Activity ``PUT /agents/{agent_id}``.
- ``{"kind": "http", "url": "{math_url}/agent/run"}`` — HTTP POST of ``agent_task``; ``{key}``
  placeholders are Runtime Config fields, so service addresses stay UI-editable.
"""

from __future__ import annotations

import json

from agents import ALL as ALL_SPECS
from agents import EXCEL_EXTRACTOR, SCHEDULE_BUILDER

EXCEL_EXTRACTOR_WF_ID = EXCEL_EXTRACTOR.resolved_workflow_id
SCHEDULE_BUILDER_WF_ID = SCHEDULE_BUILDER.resolved_workflow_id

# (column, DDL type) — order is the SELECT / INSERT order everywhere.
BASE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("agent_id", "TEXT PRIMARY KEY"),
    ("title", "TEXT"),
    ("when_to_use", "TEXT"),
    ("input_required", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ("output_provides", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
)
# Added in Phase 2 — ``ALTER TABLE … ADD COLUMN IF NOT EXISTS`` so a live database upgrades in place.
PHASE2_COLUMNS: tuple[tuple[str, str], ...] = (
    ("invoke", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
    ("input_schema", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
    ("output_schema", "JSONB NOT NULL DEFAULT '{}'::jsonb"),
    ("hitl_policy", "TEXT NOT NULL DEFAULT 'agent_asks'"),
    ("enabled", "BOOLEAN NOT NULL DEFAULT true"),
    ("version", "TEXT NOT NULL DEFAULT '1'"),
)
COLUMNS: tuple[tuple[str, str], ...] = BASE_COLUMNS + PHASE2_COLUMNS
COLUMN_NAMES: tuple[str, ...] = tuple(name for name, _ in COLUMNS)
JSON_COLUMNS: frozenset[str] = frozenset({"input_required", "output_provides", "invoke", "input_schema", "output_schema"})

# What the Decision LLM sees. ``invoke`` is a deployment detail, not a planning fact.
PLANNER_COLUMNS: tuple[str, ...] = (
    "agent_id",
    "title",
    "when_to_use",
    "input_required",
    "output_provides",
    "input_schema",
    "output_schema",
    "hitl_policy",
)

HITL_POLICIES = ("agent_asks", "never")

# Seed rows come from the agent specs (n8n/templates/agents/*.py) — one declaration per agent.
SEED: tuple[dict, ...] = tuple(spec.registry_row() for spec in ALL_SPECS)


def _sql_literal(value: object, column: str) -> str:
    if column in JSON_COLUMNS:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return "'" + text.replace("'", "''") + "'::jsonb"
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def create_table_sql(pretty: bool = False) -> str:
    """``CREATE TABLE IF NOT EXISTS`` with the base columns + ``ALTER TABLE ADD COLUMN IF NOT EXISTS`` for Phase 2."""
    sep = ",\n    " if pretty else ","
    body = sep.join(f"{name} {ddl}" for name, ddl in BASE_COLUMNS)
    create = (
        f"CREATE TABLE IF NOT EXISTS agent_registry (\n    {body}\n);" if pretty else f"CREATE TABLE IF NOT EXISTS agent_registry ({body});"
    )
    alters = [f"ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS {name} {ddl};" for name, ddl in PHASE2_COLUMNS]
    return ("\n" if pretty else "").join([create, *alters])


def seed_rows_sql(agent_ids: tuple[str, ...] | None = None, *, mode: str = "update", pretty: bool = False) -> str:
    """``INSERT … ON CONFLICT`` for the seed rows.

    ``mode="update"`` (SQL init files, applied by an operator): descriptions and invoke follow the repo.
    ``mode="fill"`` (proxy ``schema`` on every Activity boot): never overwrite what an engineer edited via
    ``upsert_agent`` — only fill Phase 2 columns that are still at their empty defaults.
    """
    rows = [row for row in SEED if not agent_ids or row["agent_id"] in agent_ids]
    cols = ", ".join(COLUMN_NAMES)
    joiner = ",\n    " if pretty else ","
    values = joiner.join("(" + ", ".join(_sql_literal(row[c], c) for c in COLUMN_NAMES) + ")" for row in rows)
    if mode == "update":
        sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMN_NAMES if c != "agent_id")
    elif mode == "fill":
        fills = []
        for c, ddl in PHASE2_COLUMNS:
            if c in JSON_COLUMNS:
                fills.append(f"{c} = CASE WHEN agent_registry.{c} = '{{}}'::jsonb THEN EXCLUDED.{c} ELSE agent_registry.{c} END")
            elif c == "hitl_policy":
                fills.append(f"{c} = CASE WHEN agent_registry.{c} = 'agent_asks' THEN EXCLUDED.{c} ELSE agent_registry.{c} END")
            elif c == "version":
                fills.append(f"{c} = CASE WHEN agent_registry.{c} = '1' THEN EXCLUDED.{c} ELSE agent_registry.{c} END")
        sets = ", ".join(fills)
    else:
        raise ValueError(mode)
    nl = "\n" if pretty else " "
    return f"INSERT INTO agent_registry ({cols}){nl}VALUES{nl}    {values}{nl}ON CONFLICT (agent_id) DO UPDATE SET {sets};"


def list_agents_sql(enabled_only: bool = False) -> str:
    where = " WHERE enabled" if enabled_only else ""
    return f"SELECT {', '.join(COLUMN_NAMES)} FROM agent_registry{where} ORDER BY agent_id"


def upsert_agent_sql() -> str:
    """Parametrised upsert used by the proxy: $1..$N in ``COLUMN_NAMES`` order; JSON columns cast."""
    placeholders = []
    for i, name in enumerate(COLUMN_NAMES, 1):
        placeholders.append(f"${i}::jsonb" if name in JSON_COLUMNS else (f"${i}::boolean" if name == "enabled" else f"${i}"))
    sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in COLUMN_NAMES if c != "agent_id")
    return (
        f"INSERT INTO agent_registry({','.join(COLUMN_NAMES)}) VALUES({','.join(placeholders)}) "
        f"ON CONFLICT(agent_id) DO UPDATE SET {sets} RETURNING {','.join(COLUMN_NAMES)}"
    )


def seed_as_python() -> list[dict]:
    """Deep copy for services that keep an in-memory registry (Activity memory mode)."""
    return json.loads(json.dumps(SEED, ensure_ascii=False))
