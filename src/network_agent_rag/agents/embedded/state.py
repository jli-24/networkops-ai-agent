"""LangGraph state for the embedded workflow.

Values are plain msgpack-safe dicts (Pydantic models are dumped before
storage) because this state is persisted into LangGraph checkpoints.
"""

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict


class EmbeddedState(TypedDict):
    task_id: str
    goal: str
    iteration: int
    hardware_design: NotRequired[dict | None]
    firmware: NotRequired[dict | None]
    approval_request: NotRequired[dict | None]
    approval_result: NotRequired[dict | None]
    compile_result: NotRequired[dict | None]
    simulation: NotRequired[dict | None]
    debug_report: NotRequired[dict | None]
    validation_state: NotRequired[str]
    error: NotRequired[str | None]
    artifacts: NotRequired[list[str]]
    final_report: NotRequired[str]


class EmbeddedInput(TypedDict):
    task_id: NotRequired[str]
    goal: str


__all__ = ["EmbeddedInput", "EmbeddedState"]
