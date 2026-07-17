"""Authentication, identity lifecycle, and role-based access control."""

from network_agent_rag.auth.authentication import (
    APIKeyProvider,
    AuthenticationProvider,
    CompositeAuthenticationProvider,
    JWTProvider,
)
from network_agent_rag.auth.assembly import open_redis_identity_authentication
from network_agent_rag.auth.context import (
    AuthorizationError,
    UserContext,
    authorizing_role,
    require_permission,
)
from network_agent_rag.auth.dependencies import current_user_dependency
from network_agent_rag.auth.models import Permission, Role, User
from network_agent_rag.auth.identity import (
    APIKeyCredential,
    EnterpriseIdentityService,
    IdentitySession,
    IdentityStore,
    InMemoryIdentityStore,
    RedisIdentityStore,
    SessionStatus,
    TokenPair,
)
from network_agent_rag.auth.rbac import has_permission, permissions_for
from network_agent_rag.auth.token import AuthenticationError, JWTTokenManager
from network_agent_rag.auth.users import UserIdentity

__all__ = [
    "AuthenticationError",
    "APIKeyCredential",
    "APIKeyProvider",
    "AuthenticationProvider",
    "CompositeAuthenticationProvider",
    "EnterpriseIdentityService",
    "IdentitySession",
    "IdentityStore",
    "InMemoryIdentityStore",
    "JWTProvider",
    "JWTTokenManager",
    "AuthorizationError",
    "Permission",
    "Role",
    "RedisIdentityStore",
    "SessionStatus",
    "TokenPair",
    "User",
    "UserIdentity",
    "UserContext",
    "authorizing_role",
    "current_user_dependency",
    "has_permission",
    "permissions_for",
    "open_redis_identity_authentication",
    "require_permission",
]
