"""Evidence-grounded text report specialist."""

from __future__ import annotations

from collections.abc import Callable

from network_agent_rag.agents.multi_agent.state import MultiAgentState, completion_update
from network_agent_rag.agents.workflow import CheckerResult


def default_report(state: MultiAgentState) -> str:
    diagnosis = state["diagnosis_result"]
    if diagnosis is None or not diagnosis["hypotheses"]:
        diagnosis_text = "当前证据不足，未形成可验证的根因假设。"
        references = "无"
    else:
        hypothesis = diagnosis["hypotheses"][0]
        diagnosis_text = (
            f"根因：{hypothesis['cause']}\n\n"
            f"规则诊断置信度 {hypothesis['confidence_percent']}%"
        )
        references = "、".join(diagnosis["evidence_refs"]) or "无"
    repair = state["repair_plan"]
    if repair is None:
        repair_text = "未生成修复计划。"
    else:
        steps = "\n".join(f"{index}. {step}" for index, step in enumerate(repair["steps"], 1))
        verification = "\n".join(f"- {step}" for step in repair["verification_steps"])
        rollback = "\n".join(f"- {step}" for step in repair["rollback_conditions"])
        repair_text = (
            f"风险等级：{repair['risk_level']}\n\n"
            "必须在维护窗口获得人工批准后由运维人员执行。\n\n"
            f"{steps}\n\n验证：\n{verification}\n\n回退条件：\n{rollback}\n\n"
            "执行状态：未执行（not_executed）。"
        )
    errors = "；".join(
        f"{agent}: {message}" for agent, message in state["agent_errors"].items()
    ) or "无"
    return f"""## 事件

事件编号：{state['incident_id']}

{diagnosis['summary'] if diagnosis is not None else state['user_query']}

## 诊断结果

{diagnosis_text}

证据引用：{references}

## 修复计划

{repair_text}

## 降级与未解决问题

{errors}

本报告只提供诊断与计划，不会执行网络变更。
"""


def default_check_report(state: MultiAgentState) -> CheckerResult:
    report = state["report"]
    unsafe = ("已自动执行", "自动执行修复", "已重启接口", "已修改配置")
    if any(term in report for term in unsafe):
        return {"approved": False, "feedback": "report contains an execution claim"}
    diagnosis = state["diagnosis_result"]
    required = ["不会执行网络变更"]
    if diagnosis is not None and diagnosis["hypotheses"]:
        hypothesis = diagnosis["hypotheses"][0]
        required.extend(
            [
                hypothesis["cause"],
                f"规则诊断置信度 {hypothesis['confidence_percent']}%",
                *diagnosis["evidence_refs"],
            ]
        )
    if state["repair_plan"] is not None:
        required.extend(["人工批准", "验证", "回退", "未执行"])
    missing = [item for item in required if item not in report]
    if missing:
        return {
            "approved": False,
            "feedback": f"report is missing grounded content: {', '.join(missing)}",
        }
    return {"approved": True, "feedback": ""}


def run_report_agent(
    state: MultiAgentState,
    *,
    generate_report: Callable[[MultiAgentState], str] | None,
    check_report: Callable[[MultiAgentState], CheckerResult] | None,
    max_quality_iterations: int,
) -> dict[str, object]:
    report = generate_report(state) if generate_report is not None else default_report(state)
    if not isinstance(report, str) or not report.strip():
        raise ValueError("generate_report must return a non-empty string")
    attempt = state["report_iteration"] + 1
    checking_state = {**state, "report": report.strip(), "answer": report.strip()}
    result = (
        check_report(checking_state) if check_report is not None else default_check_report(checking_state)
    )
    if not isinstance(result, dict):
        raise ValueError("check_report must return a CheckerResult")
    approved = result.get("approved")
    feedback = result.get("feedback")
    if not isinstance(approved, bool) or not isinstance(feedback, str):
        raise ValueError("CheckerResult requires bool approved and str feedback")
    updates: dict[str, object] = {
        "report": report.strip(),
        "answer": report.strip(),
        "report_iteration": attempt,
        "iteration": max(attempt, state["diagnosis_iteration"]),
        "report_feedback": None if approved else feedback,
        "current_agent": None,
    }
    if approved:
        return {**completion_update(state, "report"), **updates}
    if not feedback.strip():
        raise ValueError("report feedback must be non-empty when rejected")
    if attempt >= max_quality_iterations:
        return {
            **completion_update(state, "report"),
            **updates,
            "error": (
                "MAX_ITERATIONS_REACHED: report did not pass evidence checks "
                f"after {max_quality_iterations} iterations"
            ),
        }
    return {**updates, "handoff_count": state["handoff_count"] + 1}


__all__ = ["default_check_report", "default_report", "run_report_agent"]
