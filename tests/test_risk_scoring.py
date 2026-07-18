"""Deterministic governance risk scoring and audit isolation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from network_agent_rag.audit import SQLiteAuditLog
from network_agent_rag.auth import Role, UserIdentity
from network_agent_rag.governance import (
    GovernanceOperation,
    GovernanceRiskLevel,
    GovernanceService,
    RiskRequest,
    RiskScorer,
)
from network_agent_rag.observability import SQLiteTraceStore
from pydantic import ValidationError


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


def request(
    operation: GovernanceOperation,
    *,
    roles: tuple[Role, ...] = (Role.ENGINEER,),
    devices: tuple[str, ...] = ("SW1",),
) -> RiskRequest:
    return RiskRequest(
        incident_id="INC-RISK-1",
        identity=UserIdentity(
            user_id="user-1",
            username="must-not-be-audited",
            email="private@example.com",
            roles=roles,
        ),
        action="network.change",
        operation=operation,
        devices=devices,
    )


class RiskScorerTests(unittest.TestCase):
    def test_operation_baselines_cover_all_levels(self) -> None:
        scorer = RiskScorer()
        expected = {
            GovernanceOperation.VIEW_DEVICE_STATUS: (10, GovernanceRiskLevel.LOW),
            GovernanceOperation.CREATE_REPAIR_PLAN: (35, GovernanceRiskLevel.MEDIUM),
            GovernanceOperation.RESTART_DEVICE: (60, GovernanceRiskLevel.HIGH),
            GovernanceOperation.BATCH_CONFIGURATION_CHANGE: (
                80,
                GovernanceRiskLevel.CRITICAL,
            ),
        }

        for operation, (score, level) in expected.items():
            with self.subTest(operation=operation):
                result = scorer.assess(
                    request(operation),
                    authorization_denied_count=0,
                    repair_failed_count=0,
                    assessment_id="assessment-1",
                    now=NOW,
                )
                self.assertEqual((result.risk_score, result.risk_level), (score, level))

    def test_scope_and_history_modifiers_are_capped_at_one_hundred(self) -> None:
        result = RiskScorer().assess(
            request(
                GovernanceOperation.BATCH_CONFIGURATION_CHANGE,
                devices=tuple(f"SW{index}" for index in range(1, 7)),
            ),
            authorization_denied_count=20,
            repair_failed_count=20,
            assessment_id="assessment-1",
            now=NOW,
        )

        self.assertEqual(result.risk_score, 100)
        self.assertEqual(result.risk_level, GovernanceRiskLevel.CRITICAL)
        self.assertIn("device_scope:+20", result.reasons)
        self.assertIn("authorization_history:+15", result.reasons)
        self.assertIn("repair_failure_history:+15", result.reasons)

    def test_roles_do_not_change_operation_risk(self) -> None:
        scorer = RiskScorer()
        engineer = scorer.assess(
            request(GovernanceOperation.RESTART_DEVICE, roles=(Role.ENGINEER,)),
            assessment_id="assessment-1",
            now=NOW,
        )
        admin = scorer.assess(
            request(GovernanceOperation.RESTART_DEVICE, roles=(Role.ADMIN,)),
            assessment_id="assessment-2",
            now=NOW,
        )

        self.assertEqual(engineer.risk_score, admin.risk_score)
        self.assertEqual(engineer.risk_level, admin.risk_level)

    def test_unknown_operation_and_duplicate_devices_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RiskRequest(
                incident_id="INC-RISK-1",
                identity=request(GovernanceOperation.VIEW_DEVICE_STATUS).identity,
                action="read",
                operation="unknown",
                devices=("SW1",),
            )
        with self.assertRaises(ValidationError):
            RiskRequest(
                incident_id="INC-RISK-1",
                identity=request(GovernanceOperation.VIEW_DEVICE_STATUS).identity,
                action="read",
                operation=GovernanceOperation.VIEW_DEVICE_STATUS,
                devices=("SW1", "SW1"),
            )


class GovernanceRiskAuditTests(unittest.TestCase):
    def test_calculate_risk_writes_only_minimal_fixed_audit_action(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            trace = SQLiteTraceStore(Path(directory) / "trace.sqlite3")
            service = GovernanceService(
                audit,
                trace,
                clock=lambda: NOW,
                id_factory=lambda: "assessment-1",
            )

            assessment = service.calculate_risk(
                request(GovernanceOperation.CREATE_REPAIR_PLAN)
            )
            events = audit.list_events("INC-RISK-1")

        self.assertEqual(assessment.risk_score, 35)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, "risk_assessment_created")
        serialized = str(events[0].model_dump(mode="json"))
        self.assertNotIn("must-not-be-audited", serialized)
        self.assertNotIn("private@example.com", serialized)
        self.assertNotIn("UserIdentity", serialized)

    def test_audit_failure_is_propagated_without_mutating_identity(self) -> None:
        with TemporaryDirectory() as directory:
            audit = SQLiteAuditLog(Path(directory) / "audit.sqlite3")
            trace = SQLiteTraceStore(Path(directory) / "trace.sqlite3")

            class FailingAudit:
                def list_all_events(self, *, event_type=None):
                    return audit.list_all_events(event_type=event_type)

                def list_events(self, incident_id, *, event_type=None):
                    return audit.list_events(incident_id, event_type=event_type)

                def record(self, **kwargs):
                    raise OSError("audit unavailable")

            current = request(GovernanceOperation.CREATE_REPAIR_PLAN)
            before = current.identity.model_dump()
            service = GovernanceService(
                FailingAudit(),
                trace,
                clock=lambda: NOW,
                id_factory=lambda: "assessment-1",
            )

            with self.assertRaises(OSError):
                service.calculate_risk(current)

        self.assertEqual(current.identity.model_dump(), before)


if __name__ == "__main__":
    unittest.main()
