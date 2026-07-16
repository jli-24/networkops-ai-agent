"""Standalone in-memory role-based access-control foundation."""

from network_agent_rag.auth.authentication import AuthenticationProvider, JWTProvider
from network_agent_rag.auth.context import (
    AuthorizationError,
    UserContext,
    authorizing_role,
    require_permission,
)
from network_agent_rag.auth.dependencies import current_user_dependency
from network_agent_rag.auth.models import Permission, Role, User
from network_agent_rag.auth.rbac import has_permission, permissions_for
from network_agent_rag.auth.token import AuthenticationError, JWTTokenManager
from network_agent_rag.auth.users import UserIdentity

__all__ = [
    "AuthenticationError",
    "AuthenticationProvider",
    "JWTProvider",
    "JWTTokenManager",
    "AuthorizationError",
    "Permission",
    "Role",
    "User",
    "UserIdentity",
    "UserContext",
    "authorizing_role",
    "current_user_dependency",
    "has_permission",
    "permissions_for",
    "require_permission",
]
