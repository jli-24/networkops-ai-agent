"""Standalone in-memory role-based access-control foundation."""

from network_agent_rag.auth.models import Permission, Role, User
from network_agent_rag.auth.rbac import has_permission, permissions_for

__all__ = ["Permission", "Role", "User", "has_permission", "permissions_for"]
