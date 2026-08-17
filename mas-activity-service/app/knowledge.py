"""Authoring-side MAS knowledge corpus (JSON sheet) read/write helpers."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from app.settings import get_settings

NAMESPACE_LABELS = {
    "schedule_mvp": "Schedule Builder",
    "excel_protocol": "Excel Extractor",
    "orchestrator_routing": "Orchestrator",
    "specialist_template": "Specialist template",
}

_DEFAULT_NAME = "excel-agent-operating-guide.documents.json"
_DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / "n8n" / "rag" / _DEFAULT_NAME
_CORPUS_CANDIDATES = (
    _DEFAULT_CORPUS,
    Path("/corpus/rag") / _DEFAULT_NAME,
    Path("/app/n8n/rag") / _DEFAULT_NAME,
)

_lock = threading.Lock()
_corpus_override: Path | None = None


def corpus_path() -> Path:
    if _corpus_override is not None:
        return _corpus_override
    env = get_settings().mas_knowledge_corpus.strip()
    if env:
        return Path(env)
    for candidate in _CORPUS_CANDIDATES:
        if candidate.is_file():
            return candidate
    return _DEFAULT_CORPUS


def set_corpus_path(path: Path | None) -> None:
    """Test helper — point reads/writes at a temp corpus copy."""
    global _corpus_override
    _corpus_override = path


def sha256_hex(payload: str) -> str:
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_ingestible(doc: dict[str, Any]) -> bool:
    if doc.get("do_not_ingest") is True:
        return False
    if doc.get("role") == "injection_template":
        return False
    return bool(doc.get("knowledge_id") and doc.get("target_base"))


def load_corpus() -> dict[str, Any]:
    path = corpus_path()
    if not path.is_file():
        raise FileNotFoundError(f"Knowledge corpus not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise ValueError("Knowledge corpus must be an object with documents[]")
    return data


def save_corpus(data: dict[str, Any]) -> None:
    path = corpus_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
    tmp.replace(path)


def list_namespaces() -> list[dict[str, Any]]:
    data = load_corpus()
    raw = data.get("namespaces") or {}
    out: list[dict[str, Any]] = []
    for base, types in raw.items():
        out.append(
            {
                "id": base,
                "label": NAMESPACE_LABELS.get(base, base),
                "knowledge_types": list(types) if isinstance(types, list) else [],
            }
        )
    # stable order matching known agents first
    order = list(NAMESPACE_LABELS.keys())
    out.sort(key=lambda item: (order.index(item["id"]) if item["id"] in order else 99, item["id"]))
    return out


def _summary(doc: dict[str, Any]) -> dict[str, Any]:
    text = str(doc.get("text") or "")
    preview = text.strip().replace("\n", " ")
    if len(preview) > 160:
        preview = preview[:157] + "…"
    return {
        "target_base": doc.get("target_base"),
        "knowledge_id": doc.get("knowledge_id"),
        "revision": str(doc.get("revision") or "1"),
        "knowledge_type": doc.get("knowledge_type"),
        "title": doc.get("title") or doc.get("knowledge_id"),
        "keywords": list(doc.get("keywords") or []),
        "topics": list(doc.get("topics") or []),
        "task_patterns": list(doc.get("task_patterns") or []),
        "status": doc.get("status") or "active",
        "author": doc.get("author"),
        "text_preview": preview,
        "page": doc.get("page"),
        "heading": doc.get("heading"),
    }


def list_documents(target_base: str) -> list[dict[str, Any]]:
    data = load_corpus()
    namespaces = set((data.get("namespaces") or {}).keys())
    if target_base not in namespaces:
        raise KeyError(f"Unknown target_base: {target_base}")
    docs = [
        _summary(doc)
        for doc in data["documents"]
        if _is_ingestible(doc) and doc.get("target_base") == target_base
    ]
    docs.sort(key=lambda item: (str(item.get("title") or "").lower(), str(item.get("knowledge_id"))))
    return docs


def get_document(target_base: str, knowledge_id: str) -> dict[str, Any]:
    data = load_corpus()
    for doc in data["documents"]:
        if not _is_ingestible(doc):
            continue
        if doc.get("target_base") == target_base and doc.get("knowledge_id") == knowledge_id:
            return {
                "target_base": doc.get("target_base"),
                "knowledge_id": doc.get("knowledge_id"),
                "revision": str(doc.get("revision") or "1"),
                "knowledge_type": doc.get("knowledge_type"),
                "title": doc.get("title") or doc.get("knowledge_id"),
                "keywords": list(doc.get("keywords") or []),
                "topics": list(doc.get("topics") or []),
                "task_patterns": list(doc.get("task_patterns") or []),
                "status": doc.get("status") or "active",
                "author": doc.get("author"),
                "access_scope": doc.get("access_scope"),
                "text": doc.get("text") or "",
                "page": doc.get("page"),
                "heading": doc.get("heading"),
                "source_hash": doc.get("source_hash"),
                "examples": doc.get("examples") if isinstance(doc.get("examples"), list) else [],
                "has_schema_catalogue": isinstance(doc.get("schema_catalogue"), dict),
            }
    raise KeyError(f"Document not found: {target_base}/{knowledge_id}")


def _bump_revision(current: str | int | None) -> str:
    raw = str(current or "1").strip()
    if re.fullmatch(r"\d+", raw):
        return str(int(raw) + 1)
    match = re.search(r"(\d+)$", raw)
    if match:
        n = int(match.group(1)) + 1
        return raw[: match.start(1)] + str(n)
    return f"{raw}.2"


def _normalize_tags(values: Any, *, field: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise TypeError(f"{field} must be a list of strings")
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise TypeError(f"{field} must be a list of strings")
        tag = item.strip()
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out


def _sync_metadata(doc: dict[str, Any], *, source_hash: str, revision: str) -> None:
    meta = doc.get("metadata")
    if not isinstance(meta, dict):
        meta = {}
        doc["metadata"] = meta
    meta["revision"] = revision
    meta["document_revision"] = revision
    meta["source_hash"] = source_hash
    if doc.get("knowledge_id"):
        meta["knowledge_id"] = doc["knowledge_id"]
        meta["document_id"] = doc["knowledge_id"]
    if doc.get("target_base"):
        meta["target_base"] = doc["target_base"]
    if doc.get("knowledge_type"):
        meta["knowledge_type"] = doc["knowledge_type"]
    if isinstance(doc.get("keywords"), list):
        meta["keywords"] = list(doc["keywords"])
        meta["keyword_families"] = list(doc["keywords"])
    if isinstance(doc.get("topics"), list):
        meta["topics"] = list(doc["topics"])
    if isinstance(doc.get("task_patterns"), list):
        meta["task_patterns"] = list(doc["task_patterns"])


def _apply_document_fields(
    doc: dict[str, Any],
    *,
    text: str | None = None,
    title: str | None = None,
    keywords: list[str] | None = None,
    topics: list[str] | None = None,
    task_patterns: list[str] | None = None,
) -> dict[str, Any]:
    if text is not None:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if not text.strip():
            raise ValueError("text must be non-empty")
        doc["text"] = text
    if title is not None:
        title_clean = title.strip()
        if not title_clean:
            raise ValueError("title must be non-empty")
        doc["title"] = title_clean
    if keywords is not None:
        doc["keywords"] = _normalize_tags(keywords, field="keywords")
    if topics is not None:
        doc["topics"] = _normalize_tags(topics, field="topics")
    if task_patterns is not None:
        doc["task_patterns"] = _normalize_tags(task_patterns, field="task_patterns")

    body = str(doc.get("text") or "")
    new_rev = _bump_revision(doc.get("revision"))
    source_hash = sha256_hex(body)
    doc["revision"] = new_rev
    doc["source_hash"] = source_hash
    if doc.get("id") is None and doc.get("knowledge_id"):
        doc["id"] = doc["knowledge_id"]

    cat = doc.get("schema_catalogue")
    if isinstance(cat, dict):
        cat["source_hash"] = source_hash
        kid = doc.get("knowledge_id") or ""
        cat["approval_gate_id"] = f"expert:{kid}:{new_rev}"
        for schema in cat.get("schemas") or []:
            if isinstance(schema, dict):
                citation = schema.get("citation")
                if isinstance(citation, dict):
                    citation["source_hash"] = source_hash

    _sync_metadata(doc, source_hash=source_hash, revision=new_rev)
    return doc


def _apply_text_patch(doc: dict[str, Any], text: str) -> dict[str, Any]:
    return _apply_document_fields(doc, text=text)


def patch_document(
    target_base: str,
    knowledge_id: str,
    *,
    text: str | None = None,
    title: str | None = None,
    keywords: list[str] | None = None,
    topics: list[str] | None = None,
    task_patterns: list[str] | None = None,
) -> dict[str, Any]:
    if all(v is None for v in (text, title, keywords, topics, task_patterns)):
        raise ValueError("Nothing to patch")

    with _lock:
        data = load_corpus()
        namespaces = set((data.get("namespaces") or {}).keys())
        if target_base not in namespaces:
            raise KeyError(f"Unknown target_base: {target_base}")

        found = None
        for doc in data["documents"]:
            if not _is_ingestible(doc):
                continue
            if doc.get("target_base") == target_base and doc.get("knowledge_id") == knowledge_id:
                found = doc
                break
        if found is None:
            raise KeyError(f"Document not found: {target_base}/{knowledge_id}")

        _apply_document_fields(
            found,
            text=text,
            title=title,
            keywords=keywords,
            topics=topics,
            task_patterns=task_patterns,
        )
        save_corpus(data)
        return get_document(target_base, knowledge_id)


def patch_document_text(target_base: str, knowledge_id: str, text: str) -> dict[str, Any]:
    return patch_document(target_base, knowledge_id, text=text)


_KNOWLEDGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,118}$")


def create_document(
    *,
    target_base: str,
    knowledge_id: str,
    knowledge_type: str,
    title: str,
    text: str,
    keywords: list[str] | None = None,
    topics: list[str] | None = None,
    task_patterns: list[str] | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    kid = knowledge_id.strip()
    if not _KNOWLEDGE_ID_RE.fullmatch(kid):
        raise ValueError(
            "knowledge_id must be lowercase slug: start with [a-z0-9], then [a-z0-9_-], length 2–119"
        )
    title_clean = title.strip()
    if not title_clean:
        raise ValueError("title must be non-empty")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be non-empty")
    ktype = knowledge_type.strip()
    if not ktype:
        raise ValueError("knowledge_type must be non-empty")

    kw = _normalize_tags(keywords or [], field="keywords")
    tp = _normalize_tags(topics or [], field="topics")
    patterns = _normalize_tags(task_patterns or [], field="task_patterns")
    source_hash = sha256_hex(text)
    author_clean = (author or "knowledge-ui").strip() or "knowledge-ui"

    with _lock:
        data = load_corpus()
        ns_map = data.get("namespaces") or {}
        if target_base not in ns_map:
            raise KeyError(f"Unknown target_base: {target_base}")
        allowed_types = ns_map.get(target_base) or []
        if isinstance(allowed_types, list) and allowed_types and ktype not in allowed_types:
            raise ValueError(
                f"knowledge_type '{ktype}' not allowed for {target_base}; "
                f"expected one of: {', '.join(str(t) for t in allowed_types)}"
            )

        for doc in data["documents"]:
            if doc.get("target_base") == target_base and doc.get("knowledge_id") == kid:
                raise ValueError(f"Document already exists: {target_base}/{kid}")

        new_doc: dict[str, Any] = {
            "contract": "schedule_knowledge_block",
            "contract_version": "1.0",
            "target_base": target_base,
            "knowledge_type": ktype,
            "knowledge_id": kid,
            "revision": "1",
            "title": title_clean,
            "keywords": kw,
            "topics": tp,
            "task_patterns": patterns,
            "status": "active",
            "author": author_clean,
            "access_scope": "petroleum-engineering",
            "text": text,
            "source_hash": source_hash,
            "id": kid,
            "examples": [],
            "metadata": {
                "knowledge_id": kid,
                "document_id": kid,
                "target_base": target_base,
                "knowledge_type": ktype,
                "revision": "1",
                "document_revision": "1",
                "source_hash": source_hash,
                "keywords": kw,
                "keyword_families": kw,
                "topics": tp,
                "task_patterns": patterns,
            },
        }
        if target_base == "schedule_mvp":
            new_doc["simulator_family"] = ["E100", "E300", "tNavigator"]

        docs = data["documents"]
        insert_at = len(docs)
        for idx, doc in enumerate(docs):
            if doc.get("role") == "injection_template" or doc.get("do_not_ingest") is True:
                insert_at = idx
                break
        docs.insert(insert_at, new_doc)
        save_corpus(data)
        return get_document(target_base, kid)
