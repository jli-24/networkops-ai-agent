"""Deterministic, read-only log query tool for diagnosis workflows."""

from __future__ import annotations

from datetime import datetime

from langchain_core.tools import tool


_QUERY_STARTED_AT = "2026-07-12T09:00:00+08:00"
_QUERY_FINISHED_AT = "2026-07-12T09:00:00+08:00"
_KNOWN_SOURCES = {"sw1": "SW1", "sw2": "SW2"}
_RECORDS = (
    {
        "evidence_ref": "LOG-2001",
        "timestamp": "2026-07-12T08:54:00+08:00",
        "source_id": "SW1",
        "interface_name": "Gi0/1",
        "event_type": "crc_threshold",
        "severity": "warning",
        "message": "CRC error counter increased by 320 during the sample window.",
    },
    {
        "evidence_ref": "LOG-2002",
        "timestamp": "2026-07-12T08:56:00+08:00",
        "source_id": "SW2",
        "interface_name": "Gi0/24",
        "event_type": "low_optical_rx",
        "severity": "major",
        "message": "Optical receive power -20.1 dBm crossed the -18.0 dBm low threshold.",
    },
)


def _error(error_code: str, message: str) -> dict[str, object]:
    return {
        "ok": False,
        "error_code": error_code,
        "message": message,
        "count": 0,
        "records": [],
        "evidence_refs": [],
        "started_at": _QUERY_STARTED_AT,
        "finished_at": _QUERY_FINISHED_AT,
    }


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except (AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


@tool
def query_logs(
    source_ids: list[str],
    start: str,
    end: str,
    query: str,
    limit: int = 50,
) -> dict[str, object]:
    """Query a fixed, redacted log snapshot without executing device commands."""
    if not source_ids:
        return _error("INVALID_ARGUMENT", "source_ids must not be empty")

    canonical_sources: set[str] = set()
    for source_id in source_ids:
        normalized = source_id.strip().casefold()
        if not normalized:
            return _error("INVALID_ARGUMENT", "source_ids must not contain blanks")
        canonical = _KNOWN_SOURCES.get(normalized)
        if canonical is None:
            return _error("SOURCE_NOT_FOUND", f"Unknown log source: {source_id.strip()}")
        canonical_sources.add(canonical)

    start_time = _parse_time(start)
    end_time = _parse_time(end)
    if start_time is None or end_time is None or start_time > end_time:
        return _error(
            "INVALID_ARGUMENT",
            "start and end must be timezone-aware ISO 8601 values with start <= end",
        )

    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return _error("INVALID_ARGUMENT", "query must not be blank")
    if limit <= 0 or limit > 200:
        return _error("INVALID_ARGUMENT", "limit must be between 1 and 200")

    records: list[dict[str, object]] = []
    for record in _RECORDS:
        timestamp = datetime.fromisoformat(str(record["timestamp"]))
        searchable = " ".join(str(value) for value in record.values()).casefold()
        if (
            record["source_id"] in canonical_sources
            and start_time <= timestamp <= end_time
            and any(term in searchable for term in terms)
        ):
            records.append(dict(record))

    records.sort(key=lambda item: (str(item["timestamp"]), str(item["evidence_ref"])))
    records = records[:limit]
    return {
        "ok": True,
        "count": len(records),
        "records": records,
        "evidence_refs": [record["evidence_ref"] for record in records],
        "started_at": _QUERY_STARTED_AT,
        "finished_at": _QUERY_FINISHED_AT,
    }


LOG_TOOLS = [query_logs]

__all__ = ["LOG_TOOLS", "query_logs"]
