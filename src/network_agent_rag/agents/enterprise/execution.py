"""Allowlisted, single-action-at-a-time execution helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from network_agent_rag.agents.enterprise.state import (
    ActionResult,
    EnterpriseState,
    ExecutionResult,
    RepairAction,
)


ActionHandler = Callable[[RepairAction], dict[str, object]]


class AllowlistedExecutor:
    def __init__(self, handlers: Mapping[str, ActionHandler]) -> None:
        self._handlers = dict(handlers)

    def execute(self, action: RepairAction) -> ActionResult:
        handler = self._handlers.get(action["tool_name"])
        if handler is None:
            raise ValueError(f"tool is not allowlisted: {action['tool_name']}")
        result = handler(action)
        if not isinstance(result, dict):
            raise ValueError("action handler must return a dictionary")
        status = result.get("status")
        message = result.get("message")
        if status not in {"succeeded", "failed"}:
            raise ValueError("action result status must be succeeded or failed")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("action result message must be non-empty")
        return {
            "action_id": action["action_id"],
            "status": status,
            "message": message.strip(),
        }


def execute_actions(
    state: EnterpriseState | dict[str, object],
    executor: AllowlistedExecutor | None,
) -> ExecutionResult:
    approval = state.get("approval_result")
    if isinstance(approval, dict) and approval.get("decision") == "reject":
        return {
            "status": "not_executed",
            "actions": [],
            "error_code": "APPROVAL_REJECTED",
            "message": "The repair plan was rejected; no action was executed.",
        }
    risk = state.get("risk_decision")
    if isinstance(risk, dict) and risk.get("approval_required"):
        if not isinstance(approval, dict) or approval.get("decision") != "approve":
            return {
                "status": "not_executed",
                "actions": [],
                "error_code": "APPROVAL_REQUIRED",
                "message": "Human approval is required before execution.",
            }
    if executor is None:
        return {
            "status": "blocked",
            "actions": [],
            "error_code": "EXECUTOR_NOT_CONFIGURED",
            "message": "No allowlisted executor is configured; no action was executed.",
        }
    actions = state.get("proposed_actions")
    if not isinstance(actions, list) or not actions:
        return {
            "status": "blocked",
            "actions": [],
            "error_code": "NO_EXECUTABLE_ACTIONS",
            "message": "No structured repair actions were provided.",
        }
    results: list[ActionResult] = []
    for raw_action in actions:
        try:
            result = executor.execute(raw_action)
        except Exception as error:
            results.append(
                {
                    "action_id": str(raw_action.get("action_id", "unknown")),
                    "status": "failed",
                    "message": f"Action failed with {type(error).__name__}.",
                }
            )
            return {
                "status": "failed",
                "actions": results,
                "error_code": "ACTION_EXECUTION_FAILED",
                "message": "Execution stopped after the first failed action.",
            }
        results.append(result)
        if result["status"] == "failed":
            return {
                "status": "failed",
                "actions": results,
                "error_code": "ACTION_EXECUTION_FAILED",
                "message": "Execution stopped after the first failed action.",
            }
    return {
        "status": "succeeded",
        "actions": results,
        "error_code": None,
        "message": "All approved actions completed.",
    }


__all__ = ["ActionHandler", "AllowlistedExecutor", "execute_actions"]
