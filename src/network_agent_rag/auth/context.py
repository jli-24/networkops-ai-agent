"""Runtime-only RBAC context and authorization checks."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from network_agent_rag.auth.models import Permission, Role, User
from network_agent_rag.auth.rbac import has_permission


class UserContext(BaseModel):
    """A caller identity supplied through LangGraph RunnableConfig."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user: User


class AuthorizationError(PermissionError):
    """Raised when a user lacks a required typed permission."""

    def __init__(self, user_id: str, required_permission: Permission) -> None:
        self.user_id = user_id
        self.required_permission = required_permission
        super().__init__(
            f"user {user_id!r} lacks permission {required_permission.value}"
        )


def require_permission(
    user_context: UserContext,
    permission: Permission,
) -> None:
    """Require a permission without coercing identities or permission strings."""

    if not has_permission(user_context.user, permission):
        raise AuthorizationError(user_context.user.user_id, permission)


def authorizing_role(
    user_context: UserContext,
    permission: Permission,
) -> Role | None:
    """Return the first assigned role that grants the permission."""

    for role in user_context.user.roles:
        role_user = user_context.user.model_copy(update={"roles": (role,)})
        if has_permission(role_user, permission):
            return role
    return None


__all__ = [
    "AuthorizationError",
    "UserContext",
    "authorizing_role",
    "require_permission",
]
