"""Authenticated user identity models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from network_agent_rag.auth.models import NonEmptyString, Role, User


class UserIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: NonEmptyString
    username: NonEmptyString
    email: NonEmptyString | None = None
    roles: tuple[Role, ...] = Field(min_length=1)

    @field_validator("roles")
    @classmethod
    def reject_duplicate_roles(cls, roles: tuple[Role, ...]) -> tuple[Role, ...]:
        if len(roles) != len(set(roles)):
            raise ValueError("roles must not contain duplicates")
        return roles

    def to_user(self) -> User:
        return User(
            user_id=self.user_id,
            username=self.username,
            roles=self.roles,
        )


__all__ = ["UserIdentity"]
