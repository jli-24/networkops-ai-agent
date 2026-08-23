"""Governance observability projections over existing audit and trace data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from pydantic import ValidationError

import network_agent_rag
from network_agent_rag.packs.networkops.agents.enterprise import EnterpriseState
from network_agent_rag.audit import AuditEvent, AuditEventType
from network_agent_rag.auth import Permission
from network_agent_rag.observability.governance import (
    GovernanceDecision,
    GovernanceEvent,
    GovernanceQuery,
    GovernanceSource,
)
from network_agent_rag.observability.timeline import IncidentTimelineBuilder


NOW = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)


def _event(
    event_id: str,
    *,
    action: str,
    outcome: str,
    details: dict[str, object],
    offset: int = 0,
    incident_id: str = "INC-GOV-1",
    actor: str = "authorization",
    event_type: AuditEventType = AuditEventType.DECISION,
) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        incident_id=incident_id,
        event_type=event_type,
        actor=actor,
        action=action,
        outcome=outcome,
        details=details,
        created_at=NOW + timedelta(seconds=offset),
    )


class _AuditStore:
    def __init__(self, events: list[AuditEvent]) -> None:
        self.events = events

    def list_events(
        self,
        incident_id: str,
        *,
        event_type: AuditEventType | str | None = None,
    ) -> list[AuditEvent]:
        return [
            event
            for event in self.events
            if event.incident_id == incident_id
            and (event_type is None or event.event_type == AuditEventType(event_type))
        ]

    def list_all_events(
        self,
        *,
        event_type: AuditEventType | str | None = None,
    ) -> list[AuditEvent]:
        return [
            event
            for event in self.events
            if event_type is None or event.event_type == AuditEventType(event_type)
        ]


class _TraceStore:
    def __init__(self, trace_ids: dict[str, str] | None = None) -> None:
        self.trace_ids = trace_ids or {}

    def trace_id_for_incident(self, incident_id: str) -> str | None:
        return self.trace_ids.get(incident_id)

    def list_spans(self, incident_id: str, *, run_id: str | None = None) -> list:
        return []


def _authorization_events() -> list[AuditEvent]:
    return [
        _event(
            "api-create",
            action="authorize_api_repair_plan",
            outcome="allowed",
            details={
                "actor_id": "engineer-1",
                "actor_role": "Engineer",
                "permission": Permission.CREATE_REPAIR_PLAN.value,
                "decision": "allowed",
            },
        ),
        _event(
            "workflow-create",
            action="authorize_repair_plan",
            outcome="allowed",
            details={
                "actor_id": "engineer-1",
                "actor_role": "Engineer",
                "required_permission": Permission.CREATE_REPAIR_PLAN.value,
            },
            offset=1,
        ),
        _event(
            "api-approval-denied",
            action="authorize_api_approval",
            outcome="denied",
            details={
                "actor_id": "engineer-1",
                "actor_role": "Engineer",
                "permission": Permission.APPROVE_REPAIR.value,
                "decision": "denied",
            },
            offset=2,
        ),
        _event(
            "workflow-execute",
            action="authorize_execution",
            outcome="allowed",
            details={
                "actor_id": "engineer-1",
                "actor_role": "Engineer",
                "required_permission": Permission.EXECUTE_REPAIR.value,
            },
            offset=3,
        ),
    ]


class GovernanceEventTests(unittest.TestCase):
    def test_model_is_strict_frozen_and_normalizes_time(self) -> None:
        event = GovernanceEvent(
            event_id="event-1",
            timestamp=NOW.astimezone(timezone(timedelta(hours=8))),
            incident_id="INC-1",
            actor_id="admin-1",
            actor_role="Admin",
            permission=Permission.APPROVE_REPAIR,
            decision=GovernanceDecision.ALLOWED,
            source=GovernanceSource.SYSTEM,
            trace_id=None,
        )

        self.assertEqual(event.timestamp, NOW)
        with self.assertRaises(ValidationError):
            event.actor_id = "changed"
        with self.assertRaises(ValidationError):
            GovernanceEvent(**event.model_dump(), extra_field=True)

    def test_audit_trace_id_is_a_schema_neutral_projection(self) -> None:
        direct = _event(
            "direct",
            action="authorize_repair_plan",
            outcome="allowed",
            details={"trace_id": "trace-direct"},
        )
        nested = _event(
            "nested",
            action="trace",
            outcome="succeeded",
            details={"trace_event": {"trace_id": "trace-nested"}},
            event_type=AuditEventType.TRACE,
        )
        legacy = _event(
            "legacy",
            action="plan_incident",
            outcome="succeeded",
            details={},
        )

        self.assertEqual(direct.trace_id, "trace-direct")
        self.assertEqual(nested.trace_id, "trace-nested")
        self.assertIsNone(legacy.trace_id)
        self.assertNotIn("trace_id", legacy.model_dump())
        self.assertNotIn("trace_id", AuditEvent.model_json_schema()["properties"])


class GovernanceQueryTests(unittest.TestCase):
    def test_projects_predefined_api_view_permissions(self) -> None:
        events = [
            _event(
                "view-incident",
                action="authorize_api_view_incident",
                outcome="allowed",
                details={
                    "actor_id": "operator-1",
                    "actor_role": "Operator",
                    "permission": Permission.VIEW_INCIDENT.value,
                    "decision": "allowed",
                },
            ),
            _event(
                "view-trace",
                action="authorize_api_view_trace",
                outcome="allowed",
                details={
                    "actor_id": "operator-1",
                    "actor_role": "Operator",
                    "permission": Permission.VIEW_TRACE.value,
                    "decision": "allowed",
                },
            ),
        ]

        projected = GovernanceQuery(_AuditStore(events)).query()

        self.assertEqual(
            [event.permission for event in projected],
            [Permission.VIEW_INCIDENT, Permission.VIEW_TRACE],
        )

    def test_projects_fixed_actions_and_supports_combined_filters(self) -> None:
        events = _authorization_events() + [
            _event(
                "unrelated",
                action="evaluate_risk",
                outcome="proceed",
                details={},
            ),
            _event(
                "malformed",
                action="authorize_api_execution",
                outcome="allowed",
                details={"actor_id": "engineer-1", "permission": "NOT_REAL"},
            ),
        ]
        query = GovernanceQuery(
            _AuditStore(events),
            _TraceStore({"INC-GOV-1": "trace-1"}),
        )

        projected = query.query()
        denied = query.query(
            incident_id="INC-GOV-1",
            actor_id="engineer-1",
            permission=Permission.APPROVE_REPAIR,
            decision=GovernanceDecision.DENIED,
        )

        self.assertEqual(
            [event.event_id for event in projected],
            [
                "api-create",
                "workflow-create",
                "api-approval-denied",
                "workflow-execute",
            ],
        )
        self.assertEqual(
            [event.source for event in projected],
            [
                GovernanceSource.API,
                GovernanceSource.WORKFLOW,
                GovernanceSource.API,
                GovernanceSource.WORKFLOW,
            ],
        )
        self.assertEqual(
            [event.event_id for event in denied],
            ["api-approval-denied"],
        )
        self.assertTrue(all(event.trace_id == "trace-1" for event in projected))

    def test_prefers_audit_trace_id_and_handles_denied_creation_without_trace(self) -> None:
        event = _event(
            "denied-create",
            action="authorize_api_repair_plan",
            outcome="denied",
            details={
                "actor_id": "admin-1",
                "actor_role": "Admin",
                "permission": Permission.CREATE_REPAIR_PLAN.value,
                "decision": "denied",
                "trace_id": "audit-trace",
            },
        )
        direct = GovernanceQuery(_AuditStore([event]), _TraceStore()).query()[0]
        no_trace_event = event.model_copy(
            update={
                "event_id": "no-trace",
                "details": {
                    key: value
                    for key, value in event.details.items()
                    if key != "trace_id"
                },
            }
        )
        without_trace = GovernanceQuery(
            _AuditStore([no_trace_event]), _TraceStore()
        ).query()[0]

        self.assertEqual(direct.trace_id, "audit-trace")
        self.assertIsNone(without_trace.trace_id)

    def test_empty_or_legacy_audit_is_compatible(self) -> None:
        unrelated = _event(
            "legacy",
            action="plan_incident",
            outcome="succeeded",
            details={},
        )
        query = GovernanceQuery(_AuditStore([unrelated]))

        self.assertEqual(query.query(), [])
        self.assertEqual(query.metrics().authorization_total, 0)


class AuthorizationMetricsTests(unittest.TestCase):
    def test_counts_api_and_workflow_decisions_by_permission(self) -> None:
        metrics = GovernanceQuery(_AuditStore(_authorization_events())).metrics()
        by_permission = {item.permission: item for item in metrics.by_permission}

        self.assertEqual(metrics.authorization_total, 4)
        self.assertEqual(metrics.authorization_allowed, 3)
        self.assertEqual(metrics.authorization_denied, 1)
        self.assertEqual(metrics.authorization_failure_rate, 25.0)
        self.assertEqual(len(by_permission), len(Permission))
        self.assertEqual(by_permission[Permission.CREATE_REPAIR_PLAN].total, 2)
        self.assertEqual(by_permission[Permission.APPROVE_REPAIR].denied, 1)
        self.assertEqual(by_permission[Permission.EXECUTE_REPAIR].allowed, 1)
        self.assertEqual(by_permission[Permission.MANAGE_SYSTEM].total, 0)


class GovernanceTimelineTests(unittest.TestCase):
    def test_maps_authorization_events_without_faking_business_order(self) -> None:
        events = [
            _authorization_events()[0],
            _event(
                "incident-plan",
                action="plan_incident",
                outcome="succeeded",
                details={},
                offset=1,
                actor="Supervisor",
            ),
            _authorization_events()[1].model_copy(
                update={"created_at": NOW + timedelta(seconds=2)}
            ),
            _event(
                "repair-plan",
                action="run",
                outcome="succeeded",
                details={},
                offset=3,
                actor="RepairAgent",
                event_type=AuditEventType.AGENT_CALL,
            ),
            _event(
                "approval",
                action="authorize_approval",
                outcome="allowed",
                details={
                    "actor_id": "admin-1",
                    "actor_role": "Admin",
                    "required_permission": Permission.APPROVE_REPAIR.value,
                },
                offset=4,
            ),
            _event(
                "execution",
                action="authorize_execution",
                outcome="allowed",
                details={
                    "actor_id": "engineer-1",
                    "actor_role": "Engineer",
                    "required_permission": Permission.EXECUTE_REPAIR.value,
                },
                offset=5,
            ),
            _event(
                "repair-result",
                action="ACTION-1",
                outcome="succeeded",
                details={},
                offset=6,
                actor="Execute",
                event_type=AuditEventType.TOOL_CALL,
            ),
        ]
        trace_store = _TraceStore({"INC-GOV-1": "trace-1"})

        timeline = IncidentTimelineBuilder(trace_store, _AuditStore(events)).build(
            "INC-GOV-1"
        )

        self.assertEqual(
            [event.event_id for event in timeline],
            [
                "api-create",
                "incident-plan",
                "workflow-create",
                "repair-plan",
                "approval",
                "execution",
                "repair-result",
            ],
        )
        authorization = [
            event for event in timeline if event.event_type == "authorization"
        ]
        self.assertEqual(len(authorization), 4)
        self.assertEqual(authorization[0].details["source"], "api")
        self.assertEqual(
            authorization[0].details["permission"],
            Permission.CREATE_REPAIR_PLAN.value,
        )
        self.assertEqual(authorization[0].details["trace_id"], "trace-1")

    def test_governance_never_enters_workflow_state(self) -> None:
        forbidden = {"governance", "governance_events", "authorization_metrics"}

        self.assertTrue(forbidden.isdisjoint(EnterpriseState.__annotations__))
        self.assertEqual(network_agent_rag.__version__, "0.16.0")


if __name__ == "__main__":
    unittest.main()
