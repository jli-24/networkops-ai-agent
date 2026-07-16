"""Deployment-only HTTP and governance Prometheus exposition."""

from __future__ import annotations

from collections import Counter, defaultdict
from threading import Lock
from time import perf_counter
from typing import Any
import asyncio

from starlette.responses import PlainTextResponse, Response

from network_agent_rag.observability.governance import GovernanceQuery
from network_agent_rag.observability.metrics import MetricsService


class RequestMetrics:
    """Small process-local HTTP metrics registry for the single-worker image."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: Counter[tuple[str, str, str]] = Counter()
        self._errors: Counter[tuple[str, str]] = Counter()
        self._duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._duration_count: Counter[tuple[str, str]] = Counter()

    def record(
        self,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        status_class = f"{status_code // 100}xx"
        key = (method.upper(), route, status_class)
        component = (method.upper(), route)
        with self._lock:
            self._requests[key] += 1
            self._duration_sum[component] += max(0.0, duration_seconds)
            self._duration_count[component] += 1
            if status_code >= 500:
                self._errors[component] += 1

    def render_prometheus(self) -> str:
        with self._lock:
            requests = self._requests.copy()
            errors = self._errors.copy()
            duration_sum = dict(self._duration_sum)
            duration_count = self._duration_count.copy()
        lines = [
            "# HELP networkops_http_requests_total HTTP requests by route and status class.",
            "# TYPE networkops_http_requests_total counter",
        ]
        for (method, route, status_class), count in sorted(requests.items()):
            labels = _labels(method=method, route=route, status_class=status_class)
            lines.append(f"networkops_http_requests_total{{{labels}}} {count}")
        lines.extend(
            [
                "# HELP networkops_http_request_duration_seconds HTTP request duration.",
                "# TYPE networkops_http_request_duration_seconds summary",
            ]
        )
        for (method, route), value in sorted(duration_sum.items()):
            labels = _labels(method=method, route=route)
            lines.append(
                f"networkops_http_request_duration_seconds_sum{{{labels}}} {value:.9f}"
            )
            lines.append(
                f"networkops_http_request_duration_seconds_count{{{labels}}} {duration_count[(method, route)]}"
            )
        lines.extend(
            [
                "# HELP networkops_http_request_errors_total HTTP 5xx responses.",
                "# TYPE networkops_http_request_errors_total counter",
            ]
        )
        for (method, route), count in sorted(errors.items()):
            lines.append(
                f"networkops_http_request_errors_total{{{_labels(method=method, route=route)}}} {count}"
            )
        lines.extend(
            [
                "# HELP networkops_http_request_error_rate HTTP 5xx responses divided by requests.",
                "# TYPE networkops_http_request_error_rate gauge",
            ]
        )
        totals: Counter[tuple[str, str]] = Counter()
        for (method, route, _status_class), count in requests.items():
            totals[(method, route)] += count
        for (method, route), total in sorted(totals.items()):
            rate = errors[(method, route)] / total if total else 0.0
            lines.append(
                f"networkops_http_request_error_rate{{{_labels(method=method, route=route)}}} {rate:.9f}"
            )
        return "\n".join(lines) + "\n"


class DeploymentMetricsMiddleware:
    """Collect requests and own the production-only combined `/metrics` view."""

    def __init__(self, app: Any, *, enabled: bool, registry: RequestMetrics) -> None:
        self.app = app
        self.enabled = enabled
        self.registry = registry

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("path") == "/metrics":
            if not self.enabled:
                await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
                return
            started = perf_counter()
            application = scope.get("app")
            content = await asyncio.to_thread(
                render_deployment_metrics,
                getattr(application.state, "trace_store", None),
                getattr(application.state, "audit_log", None),
                self.registry,
            )
            self.registry.record("GET", "/metrics", 200, perf_counter() - started)
            await Response(
                content=content,
                media_type="text/plain; version=0.0.4",
            )(scope, receive, send)
            return

        started = perf_counter()
        status_code = 500
        recorded = False

        async def observed_send(message: dict[str, Any]) -> None:
            nonlocal status_code, recorded
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                self.registry.record(
                    str(scope.get("method", "UNKNOWN")),
                    _route_template(scope),
                    status_code,
                    perf_counter() - started,
                )
                recorded = True
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        except Exception:
            if not recorded:
                self.registry.record(
                    str(scope.get("method", "UNKNOWN")),
                    _route_template(scope),
                    500,
                    perf_counter() - started,
                )
            raise


def render_deployment_metrics(
    trace_store: Any,
    audit_log: Any,
    registry: RequestMetrics,
) -> str:
    parts: list[str] = []
    if trace_store is not None:
        parts.append(MetricsService(trace_store).render_prometheus())
    if audit_log is not None:
        authorization = GovernanceQuery(audit_log, trace_store).metrics()
        parts.extend(
            [
                "# HELP networkops_authorization_total Authorization decisions by permission and decision.\n",
                "# TYPE networkops_authorization_total counter\n",
            ]
        )
        for item in authorization.by_permission:
            for decision, count in (
                ("allowed", item.allowed),
                ("denied", item.denied),
            ):
                labels = _labels(
                    decision=decision,
                    permission=item.permission.value,
                )
                parts.append(f"networkops_authorization_total{{{labels}}} {count}\n")
    parts.append(registry.render_prometheus())
    return "".join(parts)


def _route_template(scope: dict[str, Any]) -> str:
    route = scope.get("route")
    value = getattr(route, "path", None)
    return value if isinstance(value, str) and value else "unmatched"


def _labels(**values: str) -> str:
    return ",".join(
        f'{name}="{_escape(value)}"' for name, value in sorted(values.items())
    )


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


__all__ = [
    "DeploymentMetricsMiddleware",
    "RequestMetrics",
    "render_deployment_metrics",
]
