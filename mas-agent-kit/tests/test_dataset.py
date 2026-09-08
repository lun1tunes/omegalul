"""Datasets — the flexible data contract between agents (``mas_agent_kit.dataset``)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from mas_agent_kit import ActivityClient, CasePacket, dataset_entry, dataset_rows, expected_names, expected_output, is_dataset, upstream_datasets
from mas_agent_kit.dataset import INLINE_ROWS_BYTES, PREVIEW_ROWS, dataset_json_bytes, dataset_name

ROWS = [{"well": "1601", "date": "2026-03-01", "rate": 120.5}, {"well": "1735", "date": "2026-04-01", "rate": 80}]
FIELDS = [{"name": "well", "type": "text", "source_column": "Скважина"}, {"name": "date", "type": "date", "source_column": "Дата"}, {"name": "rate", "type": "number", "source_column": "Дебит"}]


def test_dataset_name_is_a_latin_identifier() -> None:
    assert dataset_name("Well Events") == "well_events"
    assert dataset_name("well-events") == "well_events"
    assert dataset_name("мероприятия") == ""
    assert dataset_name("1abc") == ""
    assert dataset_name("") == ""


def test_small_dataset_travels_inline_large_one_as_preview_plus_artifact() -> None:
    small = dataset_entry("events", FIELDS, ROWS, title="Мероприятия", artifact_id="dataset_events", source={"sheet": "Лист1"})
    assert is_dataset(small) and small["row_count"] == 2 and small["rows"] == ROWS and "preview" not in small
    assert small["artifact_id"] == "dataset_events" and small["source"] == {"sheet": "Лист1"}
    big_rows = [{"well": str(1000 + i), "date": "2026-01-01", "comment": "x" * 40} for i in range(400)]
    assert len(json.dumps(big_rows, ensure_ascii=False)) > INLINE_ROWS_BYTES
    big = dataset_entry("history", FIELDS, big_rows, artifact_id="dataset_history")
    assert "rows" not in big and big["preview"] == big_rows[:PREVIEW_ROWS] and big["row_count"] == 400
    body = json.loads(dataset_json_bytes(big, big_rows).decode("utf-8"))
    assert body["rows"] == big_rows and body["name"] == "history" and "preview" not in body and "artifact_id" not in body


def test_dataset_rows_reads_inline_or_downloads_the_artifact() -> None:
    inline = dataset_entry("events", FIELDS, ROWS)
    assert dataset_rows(inline) == ROWS
    remote = dataset_entry("history", FIELDS, [{"well": "1", "x": "y" * 20000}], artifact_id="dataset_history")
    with pytest.raises(FileNotFoundError):
        dataset_rows(remote)  # no Activity to fetch from
    assert dataset_rows({"not": "a dataset"}) == []


class _Activity(BaseHTTPRequestHandler):
    def log_message(self, *_: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.endswith("/state"):
            big = dataset_entry("history", FIELDS, [{"well": "1", "x": "y" * 20000}], artifact_id="dataset_history")
            small = dataset_entry("events", FIELDS, ROWS, artifact_id="dataset_events")
            state = {"agents": {"excel_extractor": {"status": "completed", "step": 1, "data": {"history": big, "events": small, "facts": [{"well": "1601", "date": "2026-03-01"}]}}}}
            body = json.dumps({"state": state}).encode("utf-8")
            ctype = "application/json"
        elif self.path.endswith("/artifacts/dataset_history"):
            body = dataset_json_bytes({"kind": "dataset", "name": "history", "fields": FIELDS}, [{"well": "1", "x": "full"}])
            ctype = "application/json"
        else:
            body, ctype = b"{}", "application/json"
            self.send_response(404)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def activity_url():
    server = HTTPServer(("127.0.0.1", 0), _Activity)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


def test_case_packet_reads_upstream_datasets_inline_or_from_activity(activity_url: str) -> None:
    task = {"case_id": "CASE-1", "task_id": "T-2", "inputs": {"activity_base_url": activity_url}, "context": {}}
    packet = CasePacket.load(task, ActivityClient.from_task(task, agent_id="consumer"))
    assert set(packet.datasets()) == {"history", "events"}, "plain lists such as facts are not datasets"
    assert set(upstream_datasets(packet.state)) == {"history", "events"}
    assert packet.dataset("events") == ROWS
    assert packet.dataset("history") == [{"well": "1", "x": "full"}], "rows beyond the inline budget come from the artifact"
    assert packet.dataset("missing") == []


def test_expected_output_is_sanitised_and_names_come_from_datasets_only() -> None:
    inputs = {
        "expected_output": {
            "datasets": [
                {"name": "Well Events", "description": "мероприятия по скважинам", "fields": [{"name": "well", "description": "скважина", "type": "text"}, {"name": "date", "type": "when"}, "rate", {"name": "well"}, {"name": "нет"}]},
                {"name": "well_events"},
                {"name": "", "fields": []},
                "junk",
            ],
            "consumers": [
                {"agent_id": "schedule_builder", "title": "Schedule Builder", "needs": {"facts": "даты ввода", "new_wells": "параметры новых скважин"}},
                {"agent_id": "demo_agent", "needs": {}},
            ],
        }
    }
    exp = expected_output(inputs)
    assert exp["datasets"] == [
        {"name": "well_events", "fields": [{"name": "well", "description": "скважина", "type": "text"}, {"name": "date"}, {"name": "rate"}], "description": "мероприятия по скважинам"}
    ]
    assert exp["consumers"] == [{"agent_id": "schedule_builder", "title": "Schedule Builder", "needs": {"facts": "даты ввода", "new_wells": "параметры новых скважин"}}]
    assert expected_names(inputs) == ["well_events"], "consumers' needs are context, not requested datasets"
    assert expected_output({}) == {} and expected_output({"expected_output": "x"}) == {} and expected_output(None) == {}
    packet = CasePacket({"inputs": inputs})
    assert packet.expected_output == exp
