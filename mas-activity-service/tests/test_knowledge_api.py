"""Knowledge corpus API tests (temp JSON sheet)."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.pop("CONTROL_PLANE_PROXY_URL", None)
os.environ["CONTROL_PLANE_REQUIRED"] = "false"

from fastapi.testclient import TestClient

from app import knowledge as knowledge_store
from app.main import app

_TESTCLIENT_OPTIONS = (
    {"backend_options": {"use_uvloop": True}}
    if importlib.util.find_spec("uvloop")
    else {}
)
client = TestClient(app, **_TESTCLIENT_OPTIONS)
KEY = {}

SAMPLE = {
    "schema_version": "1.1.0",
    "title": "test corpus",
    "namespaces": {
        "schedule_mvp": ["keyword_instruction", "worked_example"],
        "excel_protocol": ["protocol_instruction"],
    },
    "documents": [
        {
            "contract": "schedule_knowledge_block",
            "contract_version": "1.0",
            "target_base": "schedule_mvp",
            "knowledge_type": "keyword_instruction",
            "knowledge_id": "dates-test-v1",
            "revision": "1",
            "title": "DATES test",
            "keywords": ["DATES"],
            "topics": ["календарь"],
            "task_patterns": ["задать даты"],
            "status": "active",
            "author": "tester",
            "access_scope": "petroleum-engineering",
            "text": "DATES — test card.\n\n**Bold** and `code`.",
            "source_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "schema_catalogue": {
                "source_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "approval_gate_id": "expert:dates-test-v1:1",
                "schemas": [
                    {
                        "schema_id": "expert:DATES:date",
                        "citation": {
                            "heading": "DATES",
                            "source_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                        },
                    }
                ],
            },
            "metadata": {
                "knowledge_id": "dates-test-v1",
                "revision": "1",
                "source_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
            "id": "dates-test-v1",
        },
        {
            "contract": "schedule_knowledge_block",
            "contract_version": "1.0",
            "target_base": "excel_protocol",
            "knowledge_type": "protocol_instruction",
            "knowledge_id": "excel-test",
            "revision": "2",
            "title": "Excel protocol",
            "keywords": ["PROTOCOL"],
            "topics": [],
            "status": "active",
            "text": "Excel protocol text.",
            "id": "excel-test",
        },
        {
            "role": "injection_template",
            "do_not_ingest": True,
            "id": "_injection-template",
        },
    ],
}


def test_knowledge_namespaces_list_get_patch(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps(SAMPLE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    knowledge_store.set_corpus_path(corpus)
    try:
        ns = client.get("/v1/knowledge/namespaces")
        assert ns.status_code == 200
        body = ns.json()
        ids = [item["id"] for item in body["namespaces"]]
        assert ids == ["schedule_mvp", "excel_protocol"]
        assert body["namespaces"][0]["label"] == "Сборщик расписания"
        assert body["namespaces"][0]["document_count"] == 1
        assert body["namespaces"][1]["document_count"] == 1

        bad = client.get("/v1/knowledge/documents", params={"target_base": "nope"})
        assert bad.status_code == 404

        listed = client.get("/v1/knowledge/documents", params={"target_base": "schedule_mvp"})
        assert listed.status_code == 200
        docs = listed.json()["documents"]
        assert len(docs) == 1
        assert docs[0]["knowledge_id"] == "dates-test-v1"
        assert "injection" not in json.dumps(docs)

        by_path = client.get("/v1/knowledge/schedule_mvp", params={"q": "DATES"})
        assert by_path.status_code == 200
        assert by_path.json()["count"] == 1
        empty_q = client.get("/v1/knowledge/schedule_mvp", params={"q": "нет-такого-текста-xyz"})
        assert empty_q.status_code == 200
        assert empty_q.json()["count"] == 0
        tagged = client.get("/v1/knowledge/schedule_mvp", params={"tag": "календарь"})
        assert tagged.json()["count"] == 1
        no_tag = client.get("/v1/knowledge/schedule_mvp", params={"tag": "WCONPROD"})
        assert no_tag.json()["count"] == 0

        detail = client.get("/v1/knowledge/documents/schedule_mvp/dates-test-v1")
        assert detail.status_code == 200
        assert detail.json()["document"]["text"].startswith("DATES")
        assert detail.json()["document"]["has_schema_catalogue"] is True
        assert detail.json()["document"]["schema_catalogue"]["schemas"][0]["schema_id"] == "expert:DATES:date"

        revisions = client.get("/v1/knowledge/schedule_mvp/dates-test-v1/revisions")
        assert revisions.status_code == 200
        revs = revisions.json()["revisions"]
        assert revs[0]["revision"] == "1"
        assert revs[0]["source"] == "corpus"

        patched = client.patch(
            "/v1/knowledge/documents/schedule_mvp/dates-test-v1",
            headers=KEY,
            json={"text": "DATES — updated.\n\nNew paragraph."},
        )
        assert patched.status_code == 200
        doc = patched.json()["document"]
        assert doc["revision"] == "2"
        assert doc["text"].startswith("DATES — updated")
        assert doc["source_hash"].startswith("sha256:")
        assert "ingest_hint" not in patched.json()

        saved = json.loads(corpus.read_text(encoding="utf-8"))
        card = next(d for d in saved["documents"] if d.get("knowledge_id") == "dates-test-v1")
        assert card["revision"] == "2"
        assert card["schema_catalogue"]["source_hash"] == card["source_hash"]
        assert card["schema_catalogue"]["schemas"][0]["citation"]["source_hash"] == card["source_hash"]
        assert card["metadata"]["revision"] == "2"

        tagged = client.patch(
            "/v1/knowledge/documents/schedule_mvp/dates-test-v1",
            headers=KEY,
            json={
                "keywords": ["DATES", "INCLUDE"],
                "topics": ["календарь", "cutover"],
                "task_patterns": ["задать даты"],
                "title": "DATES test updated",
            },
        )
        assert tagged.status_code == 200
        assert tagged.json()["document"]["revision"] == "3"
        assert tagged.json()["document"]["keywords"] == ["DATES", "INCLUDE"]
        assert tagged.json()["document"]["title"] == "DATES test updated"

        typed = client.patch(
            "/v1/knowledge/documents/schedule_mvp/dates-test-v1",
            headers=KEY,
            json={"status": "draft", "knowledge_type": "worked_example"},
        )
        assert typed.status_code == 200
        assert typed.json()["document"]["status"] == "draft"
        assert typed.json()["document"]["knowledge_type"] == "worked_example"
        assert typed.json()["document"]["revision"] == "4"

        created = client.post(
            "/v1/knowledge/documents",
            headers=KEY,
            json={
                "target_base": "schedule_mvp",
                "knowledge_id": "new-card-v1",
                "knowledge_type": "keyword_instruction",
                "title": "New card",
                "text": "Brand new instruction.",
                "keywords": ["WELSPECS"],
                "topics": ["скважины"],
                "task_patterns": [],
            },
        )
        assert created.status_code == 200
        assert created.json()["document"]["knowledge_id"] == "new-card-v1"
        assert created.json()["document"]["revision"] == "1"

        dup = client.post(
            "/v1/knowledge/documents",
            headers=KEY,
            json={
                "target_base": "schedule_mvp",
                "knowledge_id": "new-card-v1",
                "knowledge_type": "keyword_instruction",
                "title": "Dup",
                "text": "x",
            },
        )
        assert dup.status_code == 400

        bad_type = client.post(
            "/v1/knowledge/documents",
            headers=KEY,
            json={
                "target_base": "schedule_mvp",
                "knowledge_id": "bad-type-v1",
                "knowledge_type": "protocol_instruction",
                "title": "Bad",
                "text": "x",
            },
        )
        assert bad_type.status_code == 400

        saved = json.loads(corpus.read_text(encoding="utf-8"))
        docs = saved["documents"]
        new_idx = next(i for i, d in enumerate(docs) if d.get("knowledge_id") == "new-card-v1")
        tmpl_idx = next(i for i, d in enumerate(docs) if d.get("role") == "injection_template")
        assert new_idx < tmpl_idx

        page = client.get("/knowledge")
        assert page.status_code == 200
        assert "База знаний" in page.text
        assert "knowledge.js" in page.text
        assert "Новая карточка" in page.text
        assert "Загрузить в RAG" in page.text
    finally:
        knowledge_store.set_corpus_path(None)


def test_knowledge_ingest_unconfigured(monkeypatch, tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
    knowledge_store.set_corpus_path(corpus)
    for key in (
        "ORCHESTRATOR_WEBHOOK_URL",
        "N8N_BASE_URL",
        "KNOWLEDGE_INGEST_URL",
        "ACTIVITY_HYDRATE_URL",
        "ACTIVITY_LIST_URL",
        "ACTIVITY_FEED_URL",
    ):
        monkeypatch.setenv(key, "")
    try:
        res = client.post("/v1/knowledge/ingest")
        assert res.status_code == 503
        assert "Knowledge Ingestion" in res.json()["detail"]
    finally:
        knowledge_store.set_corpus_path(None)


def test_knowledge_ingest_posts_live_corpus(monkeypatch, tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
    knowledge_store.set_corpus_path(corpus)
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = '{"ok":true}'

        def json(self) -> dict[str, object]:
            return {
                "ok": True,
                "added": 2,
                "skipped": 0,
                "total_sent": 2,
                "total_in_rag": 2,
                "status": "rag_inventory_ok",
                "message": "Добавлено 2, пропущено (уже есть) 0, всего в RAG 2 карточек.",
                "findings": [],
            }

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured["timeout"] = kwargs.get("timeout")
            captured["verify"] = kwargs.get("verify")

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, *, json: object = None, headers: object = None) -> FakeResponse:
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setenv("KNOWLEDGE_INGEST_URL", "http://n8n.test/webhook/mas-knowledge-ingest")
    monkeypatch.setattr("app.knowledge.httpx.AsyncClient", FakeClient)
    try:
        res = client.post("/v1/knowledge/ingest")
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["added"] == 2
        assert body["skipped"] == 0
        assert body["total_sent"] == 2
        assert body["total_in_rag"] == 2
        assert "Добавлено 2" in body["message"]
        payload = captured["json"]
        assert isinstance(payload, dict)
        docs = payload["documents"]
        assert isinstance(docs, list)
        assert len(docs) == 2
        assert payload["purge_superseded"] is False
        assert all(item.get("role") != "injection_template" for item in docs)
        assert any(item.get("schema_catalogue") for item in docs)
        assert captured["url"] == "http://n8n.test/webhook/mas-knowledge-ingest"
    finally:
        knowledge_store.set_corpus_path(None)


def test_knowledge_ingest_one_card_and_purge(monkeypatch, tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
    knowledge_store.set_corpus_path(corpus)
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = '{"ok":true}'

        def json(self) -> dict[str, object]:
            return {
                "ok": True,
                "added": 1,
                "skipped": 0,
                "total_sent": 1,
                "total_in_rag": 2,
                "status": "rag_inventory_ok",
                "message": "Добавлено 1, пропущено (уже есть) 0, всего в RAG 2 карточек.",
                "findings": [],
            }

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, *, json: object = None, headers: object = None) -> FakeResponse:
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setenv("KNOWLEDGE_INGEST_URL", "http://n8n.test/webhook/mas-knowledge-ingest")
    monkeypatch.setattr("app.knowledge.httpx.AsyncClient", FakeClient)
    try:
        res = client.post(
            "/v1/knowledge/ingest",
            json={
                "target_base": "schedule_mvp",
                "knowledge_id": "dates-test-v1",
                "purge_superseded": True,
            },
        )
        assert res.status_code == 200
        payload = captured["json"]
        assert isinstance(payload, dict)
        assert payload["purge_superseded"] is True
        assert payload["knowledge_id"] == "dates-test-v1"
        docs = payload["documents"]
        assert isinstance(docs, list)
        assert len(docs) == 1
        assert docs[0]["knowledge_id"] == "dates-test-v1"
    finally:
        knowledge_store.set_corpus_path(None)


def test_normalize_ingest_response_unwraps_n8n_list() -> None:
    shaped = knowledge_store.normalize_ingest_response(
        [{"json": {"inserted": 3, "skipped": 40, "distinct_documents": 43, "status": "rag_inventory_ok"}}],
        sent_count=43,
    )
    assert shaped["added"] == 3
    assert shaped["skipped"] == 40
    assert shaped["total_sent"] == 43
    assert shaped["total_in_rag"] == 43
    assert shaped["ok"] is True


def test_knowledge_page_assets() -> None:
    js = client.get("/static/knowledge.js")
    assert js.status_code == 200
    assert "persistPendingBeforeIngest" in js.text
    assert "Ключевые слова SCHEDULE" in js.text
    assert "Карточка сохранена, версия" in js.text
    assert "/v1/knowledge/" in js.text
    assert "purge_superseded" in js.text
    for banned in ("allowlisted", "JSON corpus", "tag-ветки", "Keywords (теги)", "Task patterns", "В corpus нет"):
        assert banned not in js.text, banned
    page = client.get("/knowledge")
    assert "Загрузить в RAG" in page.text
    assert "Удалить заменённые фрагменты" in page.text
    assert "/static/vendor/marked.min.js" in page.text
    for banned in ("fonts.googleapis.com", "fonts.gstatic.com", "cdn.jsdelivr.net"):
        assert banned not in page.text, banned
        assert banned not in client.get("/").text
        assert banned not in client.get("/registry").text
    marked = client.get("/static/vendor/marked.min.js")
    assert marked.status_code == 200
    assert "marked" in marked.text.lower()
    font = client.get("/static/vendor/fonts/ibm-plex-sans-cyrillic-400.woff2")
    assert font.status_code == 200
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert "@font-face" in css.text
    assert "IBM Plex Sans" in css.text
    assert "/static/vendor/fonts/" in css.text
    assert client.get("/static/knowledge.css").status_code == 200
    index = client.get("/")
    assert index.status_code == 200
    assert 'href="/knowledge"' in index.text
    assert "База знаний" in index.text
    assert "btn-quiet" in index.text


def test_knowledge_revisions_from_proxy(monkeypatch, tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
    knowledge_store.set_corpus_path(corpus)
    from app import control_plane

    def fake_revisions(target_base: str, knowledge_id: str) -> list[dict]:
        assert target_base == "schedule_mvp"
        assert knowledge_id == "dates-test-v1"
        return [
            {
                "revision": "2",
                "status": "active",
                "title": "DATES test",
                "content_hash": "sha256:bbb",
                "stored_at": "2026-09-19T10:00:00+00:00",
            },
            {
                "revision": "1",
                "status": "superseded",
                "title": "DATES test",
                "content_hash": "sha256:aaa",
                "stored_at": "2026-09-18T10:00:00+00:00",
            },
        ]

    monkeypatch.setattr(control_plane, "list_knowledge_revisions", fake_revisions)
    try:
        res = client.get("/v1/knowledge/schedule_mvp/dates-test-v1/revisions")
        assert res.status_code == 200
        revs = res.json()["revisions"]
        assert [row["revision"] for row in revs] == ["2", "1"]
        assert revs[0]["source"] == "postgres"
        assert revs[1]["status"] == "superseded"
    finally:
        knowledge_store.set_corpus_path(None)
