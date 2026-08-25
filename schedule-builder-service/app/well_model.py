"""Object-level view of wells in a parsed SCHEDULE timeline."""

from __future__ import annotations

from typing import Any

from .parse import ScheduleDoc, timeline_segments

WELL_KEYWORDS = {
    "WELSPECS",
    "WELLTRACK",
    "COMPDATMD",
    "WCONPROD",
    "WCONINJE",
    "WCONHIST",
    "WELOPEN",
    "WEFAC",
    "WECON",
    "WTEST",
    "WELTARG",
    "WPIMULT",
}


def is_factual_record(record: Any) -> bool:
    comment = str(getattr(record, "comment", "") or "")
    return "факт" in comment.casefold() or "fact" in comment.casefold()


def _well_name(record: Any) -> str:
    tokens = getattr(record, "tokens", None) or []
    return str(tokens[0]).strip("'\"") if tokens else ""


def _date_text(segment: dict[str, Any]) -> str | None:
    value = segment.get("date")
    return str(value) if value else None


def build_well_objects(doc: ScheduleDoc) -> list[dict[str, Any]]:
    """Build a compact, deterministic well object inventory.

    ``first_wconprod`` is the commissioning anchor. Every later WCONPROD is
    retained as a separate forecast control event and is never confused with
    commissioning.
    """
    objects: dict[str, dict[str, Any]] = {}
    for segment in timeline_segments(doc):
        date = _date_text(segment)
        for block in segment.get("blocks") or []:
            if block.keyword not in WELL_KEYWORDS:
                continue
            for record in block.records:
                well = _well_name(record)
                if not well:
                    continue
                item = objects.setdefault(
                    well,
                    {
                        "well": well,
                        "identity": {},
                        "first_wconprod": None,
                        "control_events": [],
                        "override_events": [],
                        "economic_events": [],
                        "test_events": [],
                        "status_events": [],
                        "efficiency_events": [],
                        "connection_events": [],
                        "events": [],
                    },
                )
                event = {
                    "keyword": block.keyword,
                    "date": date,
                    "tokens": list(record.tokens),
                    "comment": record.comment,
                    "factual": is_factual_record(record),
                }
                item["events"].append(event)
                if block.keyword == "WELSPECS" and not item["identity"]:
                    tokens = record.tokens
                    item["identity"] = {
                        "group": tokens[1].strip("'\"") if len(tokens) > 1 else "",
                        "tokens": list(tokens),
                    }
                if block.keyword in {"WCONPROD", "WCONINJE", "WCONHIST"}:
                    item["control_events"].append(event)
                if block.keyword == "WELTARG":
                    item["override_events"].append(event)
                elif block.keyword == "WECON":
                    item["economic_events"].append(event)
                elif block.keyword == "WTEST":
                    item["test_events"].append(event)
                elif block.keyword == "WELOPEN":
                    item["status_events"].append(event)
                elif block.keyword == "WEFAC":
                    item["efficiency_events"].append(event)
                elif block.keyword == "WPIMULT":
                    item["connection_events"].append(event)
                if block.keyword == "WCONPROD" and item["first_wconprod"] is None:
                    item["first_wconprod"] = event

    result = []
    for item in objects.values():
        controls = item["control_events"]
        item["forecast_control_count"] = max(
            0,
            sum(1 for event in controls if event["keyword"] == "WCONPROD") - 1,
        )
        item["status"] = (
            item["first_wconprod"]["tokens"][1]
            if item["first_wconprod"] and len(item["first_wconprod"]["tokens"]) > 1
            else None
        )
        item["factual_control_events"] = [
            event for event in controls if event["factual"]
        ]
        item["forecast_control_events"] = [
            event
            for event in controls
            if event["keyword"] == "WCONPROD" and not event["factual"]
        ]
        item["commissioning_wconprod"] = next(
            (event for event in item["forecast_control_events"] if event["keyword"] == "WCONPROD"),
            None,
        )
        item["latest_control"] = controls[-1] if controls else None
        item["forecast_events"] = [
            event for event in item["events"]
            if not event["factual"] and event["keyword"] != "WCONHIST"
        ]
        result.append(item)
    return sorted(result, key=lambda item: item["well"])


def find_well(objects: list[dict[str, Any]], well: str) -> dict[str, Any] | None:
    wanted = str(well or "").strip().strip("'\"")
    return next((item for item in objects if item.get("well") == wanted), None)
