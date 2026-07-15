"""Read-only repair planning specialist."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from network_agent_rag.agents.multi_agent.state import (
    MultiAgentState,
    RepairPlan,
    completion_update,
    validate_repair_plan,
)


def default_repair_plan(state: dict[str, object]) -> RepairPlan:
    diagnosis = state.get("diagnosis_result")
    if not isinstance(diagnosis, dict):
        raise ValueError("diagnosis_result is required for repair planning")
    return {
        "risk_level": "high",
        "requires_human_approval": True,
        "steps": [
            "只读检查两端接口 CRC、输入错误增量和接收光功率。",
            "在维护窗口并获得人工批准后，由运维人员更换可疑光模块。",
            "记录变更对象、时间、执行人和替换模块序列号。",
        ],
        "verification_steps": [
            "验证链路状态、丢包率、CRC 增量、光功率和业务连通性。"
        ],
        "rollback_conditions": [
            "更换后链路不稳定或错误计数继续增长时，停止变更并恢复原模块。"
        ],
        "execution_status": "not_executed",
    }


def run_repair_agent(
    state: MultiAgentState,
    build_repair_plan: Callable[[MultiAgentState], RepairPlan] | None,
) -> dict[str, object]:
    if state["diagnosis_result"] is None:
        raise ValueError("diagnosis_result is required for RepairAgent")
    plan = (
        build_repair_plan(state)
        if build_repair_plan is not None
        else default_repair_plan(cast(dict[str, object], state))
    )
    validate_repair_plan(plan)
    return {**completion_update(state, "repair"), "repair_plan": plan}


__all__ = ["default_repair_plan", "run_repair_agent"]
