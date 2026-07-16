"""Small standard-library client for the Agent FastAPI service."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
import json


@dataclass(frozen=True, slots=True)
class SSEEvent:
    event: str
    data: dict[str, object]


class FrontendAPIError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class FastAPIClient:
    def __init__(self, base_url: str, timeout: float = 60.0) -> None:
        normalized = base_url.strip().rstrip("/")
        if urlparse(normalized).scheme not in {"http", "https"}:
            raise ValueError("base_url must use http or https")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self.base_url = normalized
        self.timeout = timeout

    def get_history(self, session_id: str) -> dict[str, object]:
        query = urlencode({"session_id": session_id})
        request = Request(f"{self.base_url}/api/v1/history?{query}")
        response = self._open(request)
        with response:
            payload = _decode_json(response.read(), "INVALID_RESPONSE")
        if not isinstance(payload.get("messages"), list):
            raise FrontendAPIError("INVALID_RESPONSE", "History response is invalid.")
        return payload

    def list_incidents(
        self,
        *,
        status: str | None = None,
        risk_level: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        parameters = {"limit": limit}
        for key, value in (("status", status), ("risk_level", risk_level), ("cursor", cursor)):
            if value is not None:
                parameters[key] = value
        return self._get_json("/api/v1/observability/incidents", parameters)

    def get_incident(self, incident_id: str) -> dict[str, object]:
        return self._get_json(f"/api/v1/incidents/{quote(incident_id, safe='')}")

    def get_incident_audit(self, incident_id: str) -> dict[str, object]:
        return self._get_json(f"/api/v1/incidents/{quote(incident_id, safe='')}/audit")

    def get_incident_trace(self, incident_id: str) -> dict[str, object]:
        return self._get_json(
            f"/api/v1/observability/incidents/{quote(incident_id, safe='')}/trace"
        )

    def get_incident_timeline(self, incident_id: str) -> dict[str, object]:
        return self._get_json(
            f"/api/v1/observability/incidents/{quote(incident_id, safe='')}/timeline"
        )

    def get_metrics_summary(self) -> dict[str, object]:
        return self._get_json("/api/v1/observability/metrics/summary")

    def list_benchmark_runs(self) -> dict[str, object]:
        return self._get_json("/api/v1/benchmarks/runs")

    def get_benchmark_run(self, run_id: str) -> dict[str, object]:
        return self._get_json(f"/api/v1/benchmarks/runs/{quote(run_id, safe='')}")

    def stream_incident(
        self,
        session_id: str,
        query: str,
        incident_id: str | None = None,
    ) -> Iterator[SSEEvent]:
        payload: dict[str, object] = {"session_id": session_id, "query": query}
        if incident_id is not None:
            payload["incident_id"] = incident_id
        return self._stream_json(
            "/api/v1/incidents",
            payload,
            terminal_events={"answer", "approval_required", "error"},
        )

    def stream_approval(
        self,
        incident_id: str,
        *,
        decision: str,
        actor: str,
        plan_digest: str,
        comment: str | None = None,
    ) -> Iterator[SSEEvent]:
        payload: dict[str, object] = {
            "decision": decision,
            "actor": actor,
            "plan_digest": plan_digest,
        }
        if comment is not None:
            payload["comment"] = comment
        return self._stream_json(
            f"/api/v1/incidents/{quote(incident_id, safe='')}/approval",
            payload,
            terminal_events={"answer", "error"},
        )

    def stream_chat(self, session_id: str, query: str) -> Iterator[SSEEvent]:
        return self._stream_json(
            "/api/v1/chat",
            {"session_id": session_id, "query": query},
            terminal_events={"answer", "error"},
        )

    def _stream_json(
        self,
        path: str,
        payload: dict[str, object],
        *,
        terminal_events: set[str],
    ) -> Iterator[SSEEvent]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        response = self._open(request)
        terminal_event = False
        with response:
            for event in _parse_sse(response):
                terminal_event = terminal_event or event.event in terminal_events
                yield event
        if not terminal_event:
            raise FrontendAPIError(
                "INCOMPLETE_STREAM",
                "Agent stream ended without an answer.",
            )

    def _get_json(
        self,
        path: str,
        parameters: dict[str, object] | None = None,
    ) -> dict[str, object]:
        query = f"?{urlencode(parameters)}" if parameters else ""
        response = self._open(Request(f"{self.base_url}{path}{query}"))
        with response:
            return _decode_json(response.read(), "INVALID_RESPONSE")

    def _open(self, request: Request) -> Any:
        try:
            return urlopen(request, timeout=self.timeout)
        except HTTPError as exc:
            message = _http_error_message(exc)
            raise FrontendAPIError(f"HTTP_{exc.code}", message) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise FrontendAPIError(
                "CONNECTION_ERROR",
                f"Unable to connect to the Agent API: {exc}",
            ) from exc


def _parse_sse(lines: Iterable[bytes | str]) -> Iterator[SSEEvent]:
    event_name: str | None = None
    data_lines: list[str] = []

    def emit() -> SSEEvent | None:
        nonlocal event_name, data_lines
        if event_name is None and not data_lines:
            return None
        if not event_name or not data_lines:
            raise FrontendAPIError("INVALID_SSE", "SSE event is incomplete.")
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError as exc:
            raise FrontendAPIError("INVALID_SSE", "SSE data is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise FrontendAPIError("INVALID_SSE", "SSE data must be a JSON object.")
        result = SSEEvent(event_name, payload)
        event_name = None
        data_lines = []
        return result

    try:
        for raw_line in lines:
            line = (
                raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            ).rstrip("\r\n")
            if not line:
                event = emit()
                if event is not None:
                    yield event
                continue
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value
            elif field == "data":
                data_lines.append(value)
    except UnicodeDecodeError as exc:
        raise FrontendAPIError("INVALID_SSE", "SSE stream is not UTF-8.") from exc

    event = emit()
    if event is not None:
        yield event


def _decode_json(body: bytes, error_code: str) -> dict[str, object]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrontendAPIError(error_code, "API response is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise FrontendAPIError(error_code, "API response must be a JSON object.")
    return payload


def _http_error_message(error: HTTPError) -> str:
    try:
        payload = _decode_json(error.read(), "INVALID_RESPONSE")
    except FrontendAPIError:
        return f"Agent API returned HTTP {error.code}."
    detail = payload.get("detail")
    return detail if isinstance(detail, str) else f"Agent API returned HTTP {error.code}."


__all__ = ["FastAPIClient", "FrontendAPIError", "SSEEvent"]
