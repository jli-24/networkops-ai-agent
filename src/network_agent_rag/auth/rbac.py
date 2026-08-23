"""In-memory role-to-permission checks."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from network_agent_rag.auth.models import Permission, Role, User


_ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
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
        Role.EMBEDDED_ENGINEER: frozenset(
            {
                Permission.VIEW_INCIDENT,
                Permission.VIEW_TRACE,
                Permission.EMBEDDED_READ,
                Permission.EMBEDDED_GENERATE,
                Permission.EMBEDDED_SIMULATE,
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
)


def permissions_for(user: User) -> frozenset[Permission]:
    """Return the immutable union of permissions granted by a user's roles."""

    return frozenset(
        permission
        for role in user.roles
        for permission in _ROLE_PERMISSIONS[role]
    )


def has_permission(user: User, permission: Permission) -> bool:
    """Return whether a valid user has the requested typed permission."""

    if not isinstance(user, User) or not isinstance(permission, Permission):
        return False
    return permission in permissions_for(user)


__all__ = ["has_permission", "permissions_for"]
