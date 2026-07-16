"""Redis adapter limited to LangGraph checkpoint persistence."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.redis.aio import AsyncRedisSaver


@asynccontextmanager
async def open_redis_checkpointer(redis_url: str) -> AsyncIterator[AsyncRedisSaver]:
    if not isinstance(redis_url, str) or not redis_url.strip():
        raise ValueError("REDIS_URL must be a non-empty string")
    async with AsyncRedisSaver.from_conn_string(redis_url.strip()) as saver:
        await saver.asetup()
        yield saver


__all__ = ["open_redis_checkpointer"]
