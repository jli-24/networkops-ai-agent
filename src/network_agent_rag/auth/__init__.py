"""Standalone in-memory role-based access-control foundation."""

from network_agent_rag.auth.context import (
    AuthorizationError,
    UserContext,
    authorizing_role,
    require_permission,
)
from network_agent_rag.auth.models import Permission, Role, User
from network_agent_rag.auth.rbac import has_permission, permissions_for

__all__ = [
    "AuthorizationError",
    "Permission",
    "Role",
    "User",
    "UserContext",
    "authorizing_role",
    "has_permission",
    "permissions_for",
    "require_permission",
]
