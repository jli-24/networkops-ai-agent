"""Dependency-injected LangGraph workflow for network knowledge agents."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal
from typing_extensions import TypedDict

from langchain_core.documents import Document
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, RetryPolicy


Intent = Literal["knowledge", "topology", "monitoring", "hybrid", "general"]
_INTENTS = {"knowledge", "topology", "monitoring", "hybrid", "general"}
_FALLBACK_ANSWER = "Unable to complete the request after retries."
_EVIDENCE_FALLBACK = "Unable to find sufficiently relevant evidence."


class CheckerResult(TypedDict):
    """Quality-check result returned by the injected checker."""

    approved: bool
    feedback: str


class DocumentGradeResult(TypedDict):
    """Relevance assessment returned by the injected document grader."""

    relevance_score: float
    feedback: str


class AgentState(TypedDict):
    """Shared state passed between the agent workflow nodes."""

    user_query: str
    rewritten_query: str
    intent: Intent
    documents: list[Document]
    relevance_score: float | None
    grading_feedback: str | None
    topology_context: dict[str, object]
    metrics: dict[str, object]
    answer: str
    iteration: int
    checker_feedback: str | None
    error: str | None


class _AgentInput(TypedDict):
    user_query: str


def create_agent_workflow(
    *,
    analyze_query: Callable[[str], Intent | str],
    generate_answer: Callable[[AgentState], str],
    check_answer: Callable[[AgentState], CheckerResult],
    retrieve_documents: Callable[[str], list[Document]] | None = None,
    retrieve_topology: Callable[[str], dict[str, object]] | None = None,
    retrieve_metrics: Callable[[str], dict[str, object]] | None = None,
    grade_documents: Callable[[AgentState], DocumentGradeResult] | None = None,
    rewrite_query: Callable[[AgentState], str] | None = None,
    relevance_threshold: float = 0.8,
    max_iterations: int = 3,
    max_retry_attempts: int = 3,
) -> CompiledStateGraph:
    """Compile the synchronous network agent workflow."""

    if not 1 <= max_iterations <= 3:
        raise ValueError("max_iterations must be between 1 and 3")
    if not 1 <= max_retry_attempts <= 3:
        raise ValueError("max_retry_attempts must be between 1 and 3")
    if (
        isinstance(relevance_threshold, bool)
        or not isinstance(relevance_threshold, (int, float))
        or not 0 <= relevance_threshold <= 1
    ):
        raise ValueError("relevance_threshold must be between 0 and 1")

    retry_policy = RetryPolicy(
        initial_interval=0.0,
        backoff_factor=1.0,
        max_interval=0.0,
        max_attempts=max_retry_attempts,
        jitter=False,
        retry_on=(ConnectionError, TimeoutError),
    )

    def handle_error(state: AgentState, error: NodeError) -> Command:
        diagnostic = f"{error.node}: {type(error.error).__name__}: {error.error}"
        return Command(
            update={
                "user_query": state.get("user_query", ""),
                "rewritten_query": state.get("rewritten_query", ""),
                "intent": state.get("intent", "general"),
                "documents": state.get("documents", []),
                "relevance_score": state.get("relevance_score"),
                "grading_feedback": state.get("grading_feedback"),
                "topology_context": state.get("topology_context", {}),
                "metrics": state.get("metrics", {}),
                "answer": state.get("answer") or _FALLBACK_ANSWER,
                "iteration": state.get("iteration", 0),
                "checker_feedback": state.get("checker_feedback"),
                "error": diagnostic,
            },
            goto=END,
        )

    def query_analyzer(state: _AgentInput) -> dict[str, object]:
        query = state.get("user_query", "")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("user_query must be a non-empty string")
        intent = analyze_query(query)
        if intent not in _INTENTS:
            raise ValueError(f"intent must be one of {sorted(_INTENTS)}")
        return {
            "user_query": query.strip(),
            "rewritten_query": query.strip(),
            "intent": intent,
            "documents": [],
            "relevance_score": None,
            "grading_feedback": None,
            "topology_context": {},
            "metrics": {},
            "answer": "",
            "iteration": 0,
            "checker_feedback": None,
            "error": None,
        }

    def router(state: AgentState) -> dict[str, object]:
        return {}

    def route_intent(state: AgentState) -> str:
        return "generate" if state["intent"] == "general" else "retrieve"

    def retrieval(state: AgentState) -> dict[str, object]:
        query = state["rewritten_query"]
        intent = state["intent"]
        updates: dict[str, object] = {
            "iteration": state["iteration"] + 1,
            "relevance_score": None,
            "grading_feedback": None,
        }

        if intent in {"knowledge", "hybrid"}:
            if retrieve_documents is None:
                raise ValueError(f"retrieve_documents is required for {intent} intent")
            documents = retrieve_documents(query)
            if not isinstance(documents, list) or not all(
                isinstance(document, Document) for document in documents
            ):
                raise ValueError("retrieve_documents must return list[Document]")
            updates["documents"] = documents

        if intent in {"topology", "hybrid"}:
            if retrieve_topology is None:
                raise ValueError(f"retrieve_topology is required for {intent} intent")
            topology_context = retrieve_topology(query)
            if not isinstance(topology_context, dict):
                raise ValueError("retrieve_topology must return a dict")
            updates["topology_context"] = topology_context

        if intent in {"monitoring", "hybrid"}:
            if retrieve_metrics is None:
                raise ValueError(f"retrieve_metrics is required for {intent} intent")
            metrics = retrieve_metrics(query)
            if not isinstance(metrics, dict):
                raise ValueError("retrieve_metrics must return a dict")
            updates["metrics"] = metrics

        return updates

    def route_retrieval(state: AgentState) -> str:
        if state["intent"] in {"knowledge", "hybrid"}:
            return "grade"
        return "generate"

    def document_grader(state: AgentState) -> dict[str, object]:
        if grade_documents is None:
            raise ValueError(
                f"grade_documents is required for {state['intent']} intent"
            )
        result = grade_documents(state)
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
        if not isinstance(feedback, str):
            raise ValueError("DocumentGradeResult feedback must be a string")

        updates: dict[str, object] = {
            "relevance_score": float(score),
            "grading_feedback": feedback,
            "error": None,
        }
        if score < relevance_threshold:
            if not feedback.strip():
                raise ValueError("feedback must be non-empty for low relevance")
            if state["iteration"] >= max_iterations:
                updates.update(
                    {
                        "answer": _EVIDENCE_FALLBACK,
                        "error": (
                            "MAX_ITERATIONS_REACHED: no sufficiently relevant "
                            f"evidence after {max_iterations} iterations"
                        ),
                    }
                )
        return updates

    def route_grade(state: AgentState) -> str:
        if state["error"] is not None:
            return "end"
        if state["relevance_score"] is not None and (
            state["relevance_score"] >= relevance_threshold
        ):
            return "generate"
        return "rewrite"

    def query_rewrite(state: AgentState) -> dict[str, object]:
        if rewrite_query is None:
            raise ValueError("rewrite_query is required after a low relevance score")
        rewritten = rewrite_query(state)
        if not isinstance(rewritten, str) or not rewritten.strip():
            raise ValueError("rewrite_query must return a non-empty string")
        current = " ".join(state["rewritten_query"].split()).casefold()
        candidate = " ".join(rewritten.split()).casefold()
        if candidate == current:
            raise ValueError("rewrite_query must return a different query")
        return {"rewritten_query": rewritten.strip()}

    def generator(state: AgentState) -> dict[str, object]:
        answer = generate_answer(state)
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("generate_answer must return a non-empty string")
        consumes_iteration = (
            state["iteration"] == 0 or state["checker_feedback"] is not None
        )
        return {
            "answer": answer,
            "iteration": state["iteration"] + int(consumes_iteration),
        }

    def hallucination_checker(state: AgentState) -> dict[str, object]:
        result = check_answer(state)
        if not isinstance(result, dict):
            raise ValueError("check_answer must return a CheckerResult")
        approved = result.get("approved")
        feedback = result.get("feedback")
        if not isinstance(approved, bool) or not isinstance(feedback, str):
            raise ValueError("CheckerResult requires bool approved and str feedback")
        if approved:
            return {"checker_feedback": None, "error": None}
        if not feedback.strip():
            raise ValueError("feedback must be non-empty when an answer is rejected")
        if state["iteration"] >= max_iterations:
            return {
                "checker_feedback": feedback,
                "error": (
                    "MAX_ITERATIONS_REACHED: answer did not pass quality checks "
                    f"after {max_iterations} iterations"
                ),
            }
        return {"checker_feedback": feedback, "error": None}

    def route_check(state: AgentState) -> str:
        if state["error"] is not None or state["checker_feedback"] is None:
            return "end"
        return "retry"

    builder = StateGraph(
        AgentState,
        input_schema=_AgentInput,
        output_schema=AgentState,
    )
    retry_nodes = (
        ("QueryAnalyzer", query_analyzer),
        ("Retrieval", retrieval),
        ("DocumentGrader", document_grader),
        ("QueryRewrite", query_rewrite),
        ("Generator", generator),
        ("HallucinationChecker", hallucination_checker),
    )
    for name, node in retry_nodes:
        builder.add_node(
            name,
            node,
            retry_policy=retry_policy,
            error_handler=handle_error,
        )
    builder.add_node("Router", router)

    builder.add_edge(START, "QueryAnalyzer")
    builder.add_edge("QueryAnalyzer", "Router")
    builder.add_conditional_edges(
        "Router",
        route_intent,
        {"retrieve": "Retrieval", "generate": "Generator"},
    )
    builder.add_conditional_edges(
        "Retrieval",
        route_retrieval,
        {"grade": "DocumentGrader", "generate": "Generator"},
    )
    builder.add_conditional_edges(
        "DocumentGrader",
        route_grade,
        {"rewrite": "QueryRewrite", "generate": "Generator", "end": END},
    )
    builder.add_edge("QueryRewrite", "Router")
    builder.add_edge("Generator", "HallucinationChecker")
    builder.add_conditional_edges(
        "HallucinationChecker",
        route_check,
        {"retry": "Generator", "end": END},
    )
    return builder.compile()


__all__ = [
    "AgentState",
    "CheckerResult",
    "DocumentGradeResult",
    "Intent",
    "create_agent_workflow",
]
