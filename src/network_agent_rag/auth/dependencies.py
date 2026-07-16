"""FastAPI authorization dependencies backed by runtime UserContext."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request

from network_agent_rag.audit import AuditEventType
from network_agent_rag.auth.authentication import AuthenticationProvider
from network_agent_rag.auth.context import (
    AuthorizationError,
    UserContext,
    authorizing_role,
    require_permission as require_context_permission,
)
from network_agent_rag.auth.models import Permission
from network_agent_rag.auth.token import AuthenticationError
from network_agent_rag.auth.users import UserIdentity


_AUTHORIZATION_ACTIONS = {
    Permission.VIEW_INCIDENT: "authorize_api_view_incident",
    Permission.VIEW_TRACE: "authorize_api_view_trace",
    Permission.CREATE_REPAIR_PLAN: "authorize_api_repair_plan",
    Permission.EXECUTE_REPAIR: "authorize_api_execution",
    Permission.APPROVE_REPAIR: "authorize_api_approval",
    Permission.MANAGE_SYSTEM: "authorize_api_system",
}


def current_user_dependency(request: Request) -> UserContext | None:
    """Return an explicitly configured request user, or legacy mode when absent."""

    authentication_provider: AuthenticationProvider | None = getattr(
        request.app.state,
        "authentication_provider",
        None,
    )
    if authentication_provider is not None:
        try:
            identity = authentication_provider.authenticate(request)
        except AuthenticationError:
            _audit_authentication(
                request,
                action="authenticate_failed",
                decision="denied",
                actor_id="anonymous",
            )
            raise
        if not isinstance(identity, UserIdentity):
            _audit_authentication(
                request,
                action="authenticate_failed",
                decision="denied",
                actor_id="anonymous",
            )
            raise AuthenticationError()
        _audit_authentication(
            request,
            action="authenticate_success",
            decision="allowed",
            actor_id=identity.user_id,
        )
        return UserContext(user=identity.to_user())

    provider = getattr(request.app.state, "user_context_provider", None)
    if provider is None:
        return None
    context = provider(request)
    if context is not None and not isinstance(context, UserContext):
        raise TypeError("user_context_provider must return UserContext or None")
    return context


def get_current_user_context(request: Request) -> UserContext | None:
    """Compatibility alias for the v0.5.1 dependency name."""

    return current_user_dependency(request)


def require_permission(
    permission: Permission,
) -> Callable[[Request, UserContext | None], UserContext | None]:
    """Build a FastAPI dependency for one fixed permission."""

    action = _AUTHORIZATION_ACTIONS[permission]

    def dependency(
        request: Request,
        user_context: Annotated[
            UserContext | None,
            Depends(get_current_user_context),
        ],
    ) -> UserContext | None:
        provider_configured = (
            getattr(request.app.state, "authentication_provider", None) is not None
            or getattr(request.app.state, "user_context_provider", None) is not None
        )
        if user_context is None and not provider_configured:
            return None
        if user_context is None:
            _audit_authorization(
                request,
                action=action,
                decision="denied",
                actor_id="anonymous",
                actor_role=None,
                permission=permission,
            )
            raise AuthorizationError("anonymous", permission)

        role = authorizing_role(user_context, permission)
        try:
            require_context_permission(user_context, permission)
        except AuthorizationError:
            _audit_authorization(
                request,
                action=action,
                decision="denied",
                actor_id=user_context.user.user_id,
                actor_role=user_context.user.roles[0].value,
                permission=permission,
            )
            raise
        _audit_authorization(
            request,
            action=action,
            decision="allowed",
            actor_id=user_context.user.user_id,
            actor_role=role.value if role is not None else None,
            permission=permission,
        )
        return user_context

    return dependency


def _audit_authentication(
    request: Request,
    *,
    action: str,
    decision: str,
    actor_id: str,
) -> None:
    audit_log = getattr(request.app.state, "audit_log", None)
    if audit_log is None:
        return
    incident_id = str(
        request.path_params.get("incident_id")
        or getattr(request.state, "authorization_incident_id", None)
        or "api-authentication"
    )
    audit_log.record(
        incident_id=incident_id,
        event_type=AuditEventType.DECISION,
        actor="api_authentication",
        action=action,
        outcome=decision,
        details={
            "actor_id": actor_id,
            "source": "api",
            "decision": decision,
        },
    )


def _audit_authorization(
    request: Request,
    *,
    action: str,
    decision: str,
    actor_id: str,
    actor_role: str | None,
    permission: Permission,
) -> None:
    audit_log = getattr(request.app.state, "audit_log", None)
    if audit_log is None:
        return
    incident_id = str(
        getattr(request.state, "authorization_incident_id", None)
        or request.path_params.get("incident_id")
        or "api-authorization"
    )
    audit_log.record(
        incident_id=incident_id,
        event_type=AuditEventType.DECISION,
        actor="api_authorization",
        action=action,
        outcome=decision,
        details={
            "actor_id": actor_id,
            "actor_role": actor_role,
            "permission": permission.value,
            "decision": decision,
        },
    )


__all__ = [
    "current_user_dependency",
    "get_current_user_context",
    "require_permission",
]
