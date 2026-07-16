"""Tests for the standalone in-memory RBAC foundation."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

import network_agent_rag
from network_agent_rag.auth import (
    Permission,
    Role,
    User,
    has_permission,
    permissions_for,
)


class RBACModelTests(unittest.TestCase):
    def test_role_and_permission_enums_are_exact(self) -> None:
        self.assertEqual(
            {(role.name, role.value) for role in Role},
            {
                ("OPERATOR", "Operator"),
                ("ENGINEER", "Engineer"),
                ("ADMIN", "Admin"),
            },
        )
        self.assertEqual(
            {(permission.name, permission.value) for permission in Permission},
            {
                ("VIEW_INCIDENT", "VIEW_INCIDENT"),
                ("VIEW_TRACE", "VIEW_TRACE"),
                ("CREATE_REPAIR_PLAN", "CREATE_REPAIR_PLAN"),
                ("EXECUTE_REPAIR", "EXECUTE_REPAIR"),
                ("APPROVE_REPAIR", "APPROVE_REPAIR"),
                ("MANAGE_SYSTEM", "MANAGE_SYSTEM"),
            },
        )

    def test_user_normalizes_identifiers_serializes_and_is_frozen(self) -> None:
        user = User(
            user_id="  user-1  ",
            username="  alice  ",
            roles=(Role.OPERATOR, Role.ENGINEER),
        )

        self.assertEqual(user.user_id, "user-1")
        self.assertEqual(user.username, "alice")
        self.assertEqual(
            user.model_dump(mode="json"),
            {
                "user_id": "user-1",
                "username": "alice",
                "roles": ["Operator", "Engineer"],
            },
        )
        with self.assertRaises(ValidationError):
            user.username = "bob"

    def test_user_rejects_invalid_input(self) -> None:
        invalid_payloads = (
            {"user_id": " ", "username": "alice", "roles": (Role.OPERATOR,)},
            {"user_id": "user-1", "username": " ", "roles": (Role.OPERATOR,)},
            {"user_id": "user-1", "username": "alice", "roles": ()},
            {
                "user_id": "user-1",
                "username": "alice",
                "roles": (Role.OPERATOR, Role.OPERATOR),
            },
            {"user_id": "user-1", "username": "alice", "roles": ("Unknown",)},
            {"user_id": "user-1", "username": "alice", "roles": ("operator",)},
            {
                "user_id": "user-1",
                "username": "alice",
                "roles": (Role.OPERATOR,),
                "email": "alice@example.com",
            },
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                User.model_validate(payload)


class RBACPermissionTests(unittest.TestCase):
    def test_role_permissions_are_exact(self) -> None:
        expected = {
            Role.OPERATOR: frozenset(
                {Permission.VIEW_INCIDENT, Permission.VIEW_TRACE}
            ),
            Role.ENGINEER: frozenset(
                {
                    Permission.VIEW_INCIDENT,
                    Permission.VIEW_TRACE,
                    Permission.CREATE_REPAIR_PLAN,
                    Permission.EXECUTE_REPAIR,
                }
            ),
            Role.ADMIN: frozenset(
                {
                    Permission.VIEW_INCIDENT,
                    Permission.VIEW_TRACE,
                    Permission.APPROVE_REPAIR,
                    Permission.MANAGE_SYSTEM,
                }
            ),
        }

        for role, permissions in expected.items():
            with self.subTest(role=role):
                user = User(user_id="user-1", username="alice", roles=(role,))
                self.assertEqual(permissions_for(user), permissions)

    def test_engineer_and_admin_permissions_are_separated(self) -> None:
        engineer = User(
            user_id="engineer-1",
            username="engineer",
            roles=(Role.ENGINEER,),
        )
        admin = User(user_id="admin-1", username="admin", roles=(Role.ADMIN,))

        self.assertFalse(has_permission(engineer, Permission.APPROVE_REPAIR))
        self.assertFalse(has_permission(engineer, Permission.MANAGE_SYSTEM))
        self.assertFalse(has_permission(admin, Permission.CREATE_REPAIR_PLAN))
        self.assertFalse(has_permission(admin, Permission.EXECUTE_REPAIR))

    def test_multiple_roles_merge_into_an_immutable_permission_set(self) -> None:
        user = User(
            user_id="user-1",
            username="alice",
            roles=(Role.ENGINEER, Role.ADMIN),
        )

        permissions = permissions_for(user)

        self.assertIsInstance(permissions, frozenset)
        self.assertEqual(permissions, frozenset(Permission))

    def test_has_permission_accepts_only_valid_models_and_permissions(self) -> None:
        user = User(
            user_id="operator-1",
            username="operator",
            roles=(Role.OPERATOR,),
        )

        self.assertTrue(has_permission(user, Permission.VIEW_INCIDENT))
        self.assertFalse(has_permission(user, Permission.EXECUTE_REPAIR))
        self.assertFalse(has_permission(object(), Permission.VIEW_INCIDENT))  # type: ignore[arg-type]
        self.assertFalse(has_permission(user, "VIEW_INCIDENT"))  # type: ignore[arg-type]

    def test_package_version_is_0_5_1_release_candidate_1(self) -> None:
        self.assertEqual(network_agent_rag.__version__, "0.7.0")


if __name__ == "__main__":
    unittest.main()
