"""Evidence correlation and Agentic RAG specialist."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from langchain_core.documents import Document

from network_agent_rag.agents.diagnosis_workflow import (
    DiagnosisPlan,
    DiagnosisState,
    correlate_diagnosis_evidence,
)
from network_agent_rag.agents.multi_agent.state import (
    DiagnosisResult,
    MultiAgentState,
    completion_update,
)
from network_agent_rag.agents.workflow import DocumentGradeResult


def run_diagnosis_agent(
    state: MultiAgentState,
    *,
    retrieve_metrics: Callable[
        [DiagnosisPlan, dict[str, object]], dict[str, object]
    ]
    | None,
    retrieve_documents: Callable[[str], list[Document]] | None,
    grade_documents: Callable[[MultiAgentState], DocumentGradeResult] | None,
    rewrite_query: Callable[[MultiAgentState], str] | None,
    max_quality_iterations: int,
) -> dict[str, object]:
    plan = state["task_plan"]["analysis"]
    sources = set(plan["required_sources"])
    attempt = state["diagnosis_iteration"] + 1
    updates: dict[str, object] = {
        "diagnosis_iteration": attempt,
        "iteration": max(attempt, state["report_iteration"]),
    }

    device_evidence = state["device_evidence"]
    if "monitoring" in sources:
        if retrieve_metrics is None:
            raise ValueError("retrieve_metrics is required for monitoring evidence")
        device_evidence = retrieve_metrics(plan, state["topology_context"])
        if not isinstance(device_evidence, dict):
            raise ValueError("retrieve_metrics must return a dict")
        updates.update(
            {"device_evidence": device_evidence, "metrics": device_evidence}
        )

    found = state["documents"]
    if "knowledge" in sources:
        if retrieve_documents is None:
            raise ValueError("retrieve_documents is required for knowledge evidence")
        found = retrieve_documents(state["rewritten_query"])
        if not isinstance(found, list) or not all(
            isinstance(document, Document) for document in found
        ):
            raise ValueError("retrieve_documents must return list[Document]")
        updates["documents"] = found
        grading_state = cast(MultiAgentState, {**state, **updates})
        grade = _grade_documents(grading_state, grade_documents)
        updates.update(
            {
                "relevance_score": grade["relevance_score"],
                "grading_feedback": grade["feedback"],
            }
        )
        if grade["relevance_score"] < 0.8 and attempt < max_quality_iterations:
            if rewrite_query is None:
                raise ValueError("rewrite_query is required after low relevance")
            rewritten = rewrite_query(grading_state)
            if not isinstance(rewritten, str) or not rewritten.strip():
                raise ValueError("rewrite_query must return a non-empty string")
            if _normalize(rewritten) == _normalize(state["rewritten_query"]):
                raise ValueError("rewrite_query must return a different query")
            return {
                **updates,
                "rewritten_query": rewritten.strip(),
                "current_agent": None,
                "handoff_count": state["handoff_count"] + 1,
            }

    correlation_documents = found
    if updates.get("relevance_score", state["relevance_score"]) is not None and (
        float(updates.get("relevance_score", state["relevance_score"])) < 0.8
    ):
        correlation_documents = []
    correlation_state = cast(
        DiagnosisState,
        {
            **state,
            **updates,
            "analysis": plan,
            "logs": state["log_evidence"],
            "metrics": device_evidence,
            "documents": correlation_documents,
            "hypotheses": [],
            "evidence_refs": [],
        },
    )
    hypothesis, references = correlate_diagnosis_evidence(correlation_state)
    uncertainties = list(hypothesis["contradicting_evidence"])
    if updates.get("relevance_score", state["relevance_score"]) is not None and (
        float(updates.get("relevance_score", state["relevance_score"])) < 0.8
    ):
        uncertainties.append("知识库检索相关度未达到 0.8，结论仅基于其余证据。")
    result: DiagnosisResult = {
        "summary": f"{plan['devices'][0]} 到 {plan['devices'][-1]} 出现{plan['symptom']}",
        "hypotheses": [hypothesis],
        "evidence_refs": references,
        "remaining_uncertainties": uncertainties,
    }
    return {
        **completion_update(cast(MultiAgentState, {**state, **updates}), "diagnosis"),
        **updates,
        "diagnosis_result": result,
    }


def _grade_documents(
    state: MultiAgentState,
    grader: Callable[[MultiAgentState], DocumentGradeResult] | None,
) -> DocumentGradeResult:
    result: object
    if grader is None:
        scores = [
            document.metadata.get("relevance_score")
            for document in state["documents"]
            if isinstance(document.metadata.get("relevance_score"), (int, float))
            and not isinstance(document.metadata.get("relevance_score"), bool)
        ]
        result = {
            "relevance_score": max(scores, default=0.0),
            "feedback": "document metadata relevance score",
        }
    else:
        result = grader(state)
    if not isinstance(result, dict):
        raise ValueError("grade_documents must return a DocumentGradeResult")
    score = result.get("relevance_score")
    feedback = result.get("feedback")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not 0 <= score <= 1
    ):
        raise ValueError("relevance_score must be between 0 and 1")
    if not isinstance(feedback, str) or (score < 0.8 and not feedback.strip()):
        raise ValueError("low relevance feedback must be non-empty")
    return {"relevance_score": float(score), "feedback": feedback}


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


__all__ = ["run_diagnosis_agent"]
