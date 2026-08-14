"""Load the canonical static Excel-agent guide into a PGVector table.

This is a one-shot administration utility. It never talks to FastAPI and never
reads workbooks or session data.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg
import requests
from psycopg import sql
from psycopg.types.json import Jsonb

SOURCE_NAME = "excel-agent-operating-guide"
NAMESPACE = uuid.UUID("dd3f481b-59d9-4186-a6ca-1db6eb80194b")
TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


def positive_int(name: str, default: str) -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


def enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def documents_path() -> Path:
    configured = os.getenv("CONTEXT_DOCUMENTS_FILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    candidates = [
        Path(__file__).resolve().parents[1] / "n8n" / "rag" / "excel-agent-operating-guide.documents.json",
        Path("/app/excel-agent-operating-guide.documents.json"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Canonical context document was not found; set CONTEXT_DOCUMENTS_FILE")


def load_documents() -> list[dict[str, Any]]:
    path = documents_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read context documents from {path}") from error

    documents = payload.get("documents")
    if not isinstance(documents, list) or not documents:
        raise RuntimeError("Context document must contain a non-empty documents array")

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in documents:
        if not isinstance(item, dict):
            raise RuntimeError("Every context document must be an object")
        if item.get("role") == "injection_template" or item.get("do_not_ingest") is True:
            continue
        block = item.get("schedule_knowledge_block") if isinstance(item.get("schedule_knowledge_block"), dict) else item
        document_id = str(block.get("knowledge_id") or item.get("id") or "").strip()
        text = str(block.get("text") or item.get("text") or "").strip()
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {
                "slug": document_id,
                "topic": ",".join(block.get("topics") or []) or "protocol",
                "version": str(block.get("revision") or "1"),
                "target_base": str(block.get("target_base") or "excel_protocol"),
            }
        if not document_id or not text:
            raise RuntimeError("Every context document requires knowledge_id/id and text")
        if document_id in seen:
            raise RuntimeError(f"Duplicate context document id: {document_id}")
        seen.add(document_id)
        result.append({"id": document_id, "text": text, "metadata": metadata})
    if not result:
        raise RuntimeError("Context document must contain at least one ingestible Excel protocol card")
    return result


def embed(texts: list[str], *, model: str, dimensions: int) -> list[list[float]]:
    base_url = os.getenv("EMBEDDING_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    response = requests.post(
        f"{base_url}/embeddings",
        headers={
            "Authorization": f"Bearer {required('EMBEDDING_API_KEY')}",
            "Content-Type": "application/json",
        },
        json={"model": model, "input": texts, "dimensions": dimensions, "encoding_format": "float"},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    vectors = [entry["embedding"] for entry in sorted(payload["data"], key=lambda item: item["index"])]
    if len(vectors) != len(texts) or any(len(vector) != dimensions for vector in vectors):
        raise RuntimeError("Embedding endpoint returned an unexpected vector shape")
    return vectors


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


def connect_with_retry() -> psycopg.Connection:
    connection_args = {
        "host": required("POSTGRES_HOST"),
        "port": positive_int("POSTGRES_PORT", "5432"),
        "dbname": required("POSTGRES_DB"),
        "user": required("POSTGRES_USER"),
        "password": required("POSTGRES_PASSWORD"),
        "sslmode": os.getenv("POSTGRES_SSLMODE", "prefer").strip(),
        "connect_timeout": 5,
    }
    last: Exception | None = None
    for attempt in range(20):
        try:
            return psycopg.connect(**connection_args)
        except psycopg.OperationalError as error:
            last = error
            time.sleep(min(1 + attempt, 5))
    raise RuntimeError("PostgreSQL did not become available") from last


def main() -> None:
    table_name = os.getenv("RAG_TABLE_NAME", "n8n_excel_agent_context").strip()
    if not TABLE_RE.fullmatch(table_name):
        raise RuntimeError("RAG_TABLE_NAME must be a simple PostgreSQL identifier")

    model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small").strip()
    dimensions = positive_int("EMBEDDING_DIMENSIONS", "1536")
    documents = load_documents()
    vectors = embed([item["text"] for item in documents], model=model, dimensions=dimensions)
    table = sql.Identifier(table_name)

    with connect_with_retry() as connection, connection.cursor() as cursor:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        cursor.execute(
            sql.SQL(
                """CREATE TABLE IF NOT EXISTS {} (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    text text NOT NULL,
                    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
                    embedding vector({}) NOT NULL
                )"""
            ).format(table, sql.SQL(str(dimensions)))
        )

        # A table created by another embedding model must never be reused
        # silently. Check the actual PostgreSQL vector typmod before any
        # replacement delete; a mismatch is rolled back with a clear error.
        cursor.execute(
            """
            SELECT pg_catalog.format_type(attribute.atttypid, attribute.atttypmod)
            FROM pg_catalog.pg_attribute AS attribute
            JOIN pg_catalog.pg_class AS relation ON relation.oid = attribute.attrelid
            JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = current_schema()
              AND relation.relname = %s
              AND attribute.attname = 'embedding'
              AND NOT attribute.attisdropped
            """,
            (table_name,),
        )
        embedding_type = cursor.fetchone()
        expected_embedding_type = f"vector({dimensions})"
        if not embedding_type or embedding_type[0] != expected_embedding_type:
            actual = embedding_type[0] if embedding_type else "missing"
            raise RuntimeError(
                f"RAG table {table_name!r} has embedding type {actual}; "
                f"expected {expected_embedding_type}. Use a new table for this model/dimension."
            )

        if enabled("REPLACE_EXISTING_CONTEXT", default=True):
            cursor.execute(
                sql.SQL("DELETE FROM {} WHERE metadata->>'source' = %s").format(table),
                (SOURCE_NAME,),
            )

        for document, vector in zip(documents, vectors, strict=True):
            text = document["text"]
            metadata = dict(document["metadata"])
            metadata.update(
                {
                    "source": SOURCE_NAME,
                    "document_id": document["id"],
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
            row_id = uuid.uuid5(NAMESPACE, document["id"])
            cursor.execute(
                sql.SQL(
                    """INSERT INTO {} (id, text, metadata, embedding)
                       VALUES (%s, %s, %s, %s::vector)
                       ON CONFLICT (id) DO UPDATE SET
                           text = EXCLUDED.text,
                           metadata = EXCLUDED.metadata,
                           embedding = EXCLUDED.embedding"""
                ).format(table),
                (row_id, text, Jsonb(metadata), vector_literal(vector)),
            )

    print(
        json.dumps(
            {
                "status": "seeded",
                "table": table_name,
                "documents": len(documents),
                "model": model,
                "dimensions": dimensions,
            }
        )
    )


if __name__ == "__main__":
    main()
