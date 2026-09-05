"""Load approved schema_catalogue entries from the MAS corpus (no RAG at render time)."""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from .schema_models import KeywordSchema, SchemaCatalogue, SimulatorProfile

_BUNDLE = Path(__file__).resolve().parent / "data" / "schema_catalogues.json"


def content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _corpus_path() -> Path | None:
    env = str(os.getenv("SCHEDULE_CATALOGUE_PATH") or "").strip()
    candidates = [
        Path(env) if env else None,
        Path(__file__).resolve().parents[2] / "n8n" / "rag" / "excel-agent-operating-guide.documents.json",
        _BUNDLE,
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    return None


def _extract_schemas(payload: Any) -> list[dict[str, Any]]:
    documents: list[Any]
    if isinstance(payload, dict) and isinstance(payload.get("documents"), list):
        documents = payload["documents"]
    elif isinstance(payload, dict) and isinstance(payload.get("schemas"), list):
        return [row for row in payload["schemas"] if isinstance(row, dict)]
    elif isinstance(payload, list):
        documents = payload
    else:
        documents = []
    schemas: list[dict[str, Any]] = []
    seen: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            continue
        if document.get("do_not_ingest") is True or document.get("role") == "injection_template":
            continue
        if str(document.get("target_base") or "") not in {"", "schedule_mvp"}:
            continue
        catalogue = document.get("schema_catalogue")
        if not isinstance(catalogue, dict):
            continue
        for row in catalogue.get("schemas") or []:
            if not isinstance(row, dict):
                continue
            keyword = str(row.get("keyword") or "").strip().upper()
            variant = str(row.get("variant") or "default").strip() or "default"
            key = f"{keyword}::{variant}"
            if not keyword or key in seen:
                continue
            seen.add(key)
            schemas.append(row)
    return schemas


def build_service_catalogue(payload: Any | None = None) -> SchemaCatalogue:
    if payload is None:
        path = _corpus_path()
        if path is None:
            return SchemaCatalogue(
                catalogue_ref="catalogue://tnavigator/22.2/schedule-builder-service",
                approved_by="department-hydrodynamic-expert",
                schemas=[],
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
    rows = _extract_schemas(payload)
    parsed: list[KeywordSchema] = []
    skipped: list[str] = []
    for row in rows:
        label = f"{row.get('keyword') or '?'}::{row.get('variant') or 'default'}"
        try:
            parsed.append(KeywordSchema.model_validate(row))
        except Exception as exc:
            skipped.append(f"{label}: {exc}")
            continue
    fingerprint = "\n".join(
        f"{item.schema_id}|{item.schema_revision}|{item.keyword}|{item.variant}"
        for item in sorted(parsed, key=lambda item: f"{item.keyword}:{item.variant}")
    )
    digest = content_hash(fingerprint)
    catalogue = SchemaCatalogue(
        catalogue_ref="catalogue://tnavigator/22.2/schedule-builder-service",
        catalogue_hash=digest,
        source_hash=digest,
        simulator_profile=SimulatorProfile(),
        approved=True,
        approved_by="department-hydrodynamic-expert",
        author="department-hydrodynamic-expert",
        approval_gate_id="schedule-builder-service-bundle",
        schemas=parsed,
    )
    catalogue._skipped = skipped  # type: ignore[attr-defined]
    return catalogue


@lru_cache(maxsize=1)
def load_catalogue() -> SchemaCatalogue:
    return build_service_catalogue()


def dump_bundle(path: Path | None = None) -> Path:
    dest = path or _BUNDLE
    dest.parent.mkdir(parents=True, exist_ok=True)
    catalogue = build_service_catalogue()
    dest.write_text(catalogue.model_dump_json(indent=2), encoding="utf-8")
    load_catalogue.cache_clear()
    skipped = getattr(catalogue, "_skipped", []) or []
    if skipped:
        print(f"skipped {len(skipped)} schema rows")
        for row in skipped[:20]:
            print(" ", row)
    return dest


def lookup(keyword: str, variant: str = "default") -> KeywordSchema | None:
    code = str(keyword or "").strip().upper()
    var = str(variant or "").strip() or "default"
    return load_catalogue().schema_map().get(f"{code}::{var}")


def variants_for(keyword: str) -> list[KeywordSchema]:
    code = str(keyword or "").strip().upper()
    return [item for item in load_catalogue().schemas if item.keyword == code]


if __name__ == "__main__":
    written = dump_bundle()
    catalogue = load_catalogue()
    print(f"wrote {written} schemas={len(catalogue.schemas)}")
    keywords = sorted({item.keyword for item in catalogue.schemas})
    print("keywords:", ", ".join(keywords))
