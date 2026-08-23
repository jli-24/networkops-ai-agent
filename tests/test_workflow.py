"""Tests for the LangGraph agent workflow."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
import inspect
import unittest

from langchain_core.documents import Document
from langgraph.graph.state import CompiledStateGraph


def approve_documents(state: dict[str, object]) -> dict[str, object]:
    return {"relevance_score": 0.9, "feedback": "Relevant documents"}


def refine_query(state: dict[str, object]) -> str:
    return f"{state['rewritten_query']} refined"


class AgentWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agents = import_module("network_agent_rag.packs.networkops.agents")

    def _factory(self) -> Callable[..., CompiledStateGraph]:
        candidate = getattr(self.agents, "create_agent_workflow", None)
        self.assertTrue(callable(candidate))
        return candidate

    def _create_graph(
        self,
        *,
        intent: str = "general",
        generator: Callable[[dict[str, object]], str] | None = None,
        checker: Callable[[dict[str, object]], dict[str, object]] | None = None,
        document_retriever: Callable[[str], list[Document]] | None = None,
        topology_retriever: Callable[[str], dict[str, object]] | None = None,
        metrics_retriever: Callable[[str], dict[str, object]] | None = None,
        grader: Callable[[dict[str, object]], dict[str, object]] | None = approve_documents,
        rewriter: Callable[[dict[str, object]], str] | None = refine_query,
        relevance_threshold: float = 0.8,
        max_iterations: int = 3,
        max_retry_attempts: int = 3,
    ) -> CompiledStateGraph:
        factory = self._factory()
        parameters = inspect.signature(factory).parameters
        self.assertIn("grade_documents", parameters)
        self.assertIn("rewrite_query", parameters)
        self.assertIn("relevance_threshold", parameters)
        return factory(
            analyze_query=lambda query: intent,
            retrieve_documents=document_retriever,
            retrieve_topology=topology_retriever,
            retrieve_metrics=metrics_retriever,
            grade_documents=grader,
            rewrite_query=rewriter,
            generate_answer=generator or (lambda state: "generated answer"),
            check_answer=checker
            or (lambda state: {"approved": True, "feedback": ""}),
            max_iterations=max_iterations,
            max_retry_attempts=max_retry_attempts,
            relevance_threshold=relevance_threshold,
        )

    def test_exports_state_types_and_builds_the_seven_node_graph(self) -> None:
        self.assertIsNotNone(getattr(self.agents, "AgentState", None))
        self.assertIsNotNone(getattr(self.agents, "CheckerResult", None))
        self.assertIsNotNone(getattr(self.agents, "DocumentGradeResult", None))
        self.assertIsNotNone(getattr(self.agents, "Intent", None))

        graph = self._create_graph()
        self.assertIsInstance(graph, CompiledStateGraph)
        public_nodes = {
            node
            for node in graph.get_graph().nodes
            if not node.startswith("__")
        }
        self.assertEqual(
            public_nodes,
            {
                "QueryAnalyzer",
                "Router",
                "Retrieval",
                "DocumentGrader",
                "QueryRewrite",
                "Generator",
                "HallucinationChecker",
            },
        )
        input_schema = graph.get_input_jsonschema()
        output_schema = graph.get_output_jsonschema()
        self.assertEqual(set(input_schema["properties"]), {"user_query"})
        self.assertEqual(input_schema["required"], ["user_query"])
        self.assertEqual(
            set(output_schema["required"]),
            {
                "user_query",
                "rewritten_query",
                "intent",
                "documents",
                "relevance_score",
                "grading_feedback",
                "topology_context",
                "metrics",
                "answer",
                "iteration",
                "checker_feedback",
                "error",
            },
        )

        result = graph.invoke({"user_query": "Explain BGP"})
        self.assertEqual(
            set(result),
            {
                "user_query",
                "rewritten_query",
                "intent",
                "documents",
                "relevance_score",
                "grading_feedback",
                "topology_context",
                "metrics",
                "answer",
                "iteration",
                "checker_feedback",
                "error",
            },
        )
        self.assertEqual(result["intent"], "general")
        self.assertEqual(result["rewritten_query"], "Explain BGP")
        self.assertIsNone(result["relevance_score"])
        self.assertIsNone(result["grading_feedback"])
        self.assertEqual(result["iteration"], 1)
        self.assertIsNone(result["checker_feedback"])
        self.assertIsNone(result["error"])

    def test_routes_each_intent_to_only_its_required_context_providers(self) -> None:
        expected_calls = {
            "general": [],
            "knowledge": ["documents", "grader"],
            "topology": ["topology"],
            "monitoring": ["metrics"],
            "hybrid": ["documents", "topology", "metrics", "grader"],
        }

        for intent, expected in expected_calls.items():
            with self.subTest(intent=intent):
                calls: list[str] = []

                def documents(query: str) -> list[Document]:
                    calls.append("documents")
                    return [Document(page_content="BGP knowledge")]

                def topology(query: str) -> dict[str, object]:
                    calls.append("topology")
                    return {"path": ["r1", "r2"]}

                def metrics(query: str) -> dict[str, object]:
                    calls.append("metrics")
                    return {"cpu_percent": 32.5}

                def grader(state: dict[str, object]) -> dict[str, object]:
                    calls.append("grader")
                    return {"relevance_score": 0.9, "feedback": "Relevant"}

                result = self._create_graph(
                    intent=intent,
                    document_retriever=documents,
                    topology_retriever=topology,
                    metrics_retriever=metrics,
                    grader=grader,
                ).invoke({"user_query": "network question"})

                self.assertEqual(calls, expected)
                self.assertEqual(bool(result["documents"]), "documents" in expected)
                self.assertEqual(
                    bool(result["topology_context"]), "topology" in expected
                )
                self.assertEqual(bool(result["metrics"]), "metrics" in expected)

    def test_document_score_at_threshold_goes_directly_to_generator(self) -> None:
        queries: list[str] = []
        rewrites = 0

        def retrieve(query: str) -> list[Document]:
            queries.append(query)
            return [Document(page_content="threshold evidence")]

        def rewrite(state: dict[str, object]) -> str:
            nonlocal rewrites
            rewrites += 1
            return "unused rewrite"

        result = self._create_graph(
            intent="knowledge",
            document_retriever=retrieve,
            grader=lambda state: {"relevance_score": 0.8, "feedback": "Enough"},
            rewriter=rewrite,
        ).invoke({"user_query": "original query"})

        self.assertEqual(queries, ["original query"])
        self.assertEqual(rewrites, 0)
        self.assertEqual(result["relevance_score"], 0.8)
        self.assertEqual(result["grading_feedback"], "Enough")
        self.assertEqual(result["iteration"], 1)
        self.assertEqual(result["answer"], "generated answer")

    def test_low_score_rewrites_query_and_searches_again(self) -> None:
        queries: list[str] = []
        scores = iter((0.2, 0.9))

        def retrieve(query: str) -> list[Document]:
            queries.append(query)
            return [Document(page_content=f"evidence for {query}")]

        def grade(state: dict[str, object]) -> dict[str, object]:
            score = next(scores)
            return {"relevance_score": score, "feedback": "Need device details"}

        def rewrite(state: dict[str, object]) -> str:
            self.assertEqual(state["grading_feedback"], "Need device details")
            return "core switch packet loss troubleshooting"

        result = self._create_graph(
            intent="knowledge",
            document_retriever=retrieve,
            grader=grade,
            rewriter=rewrite,
        ).invoke({"user_query": "switch broken"})

        self.assertEqual(
            queries,
            ["switch broken", "core switch packet loss troubleshooting"],
        )
        self.assertEqual(
            result["documents"][0].page_content,
            "evidence for core switch packet loss troubleshooting",
        )
        self.assertEqual(result["rewritten_query"], queries[-1])
        self.assertEqual(result["relevance_score"], 0.9)
        self.assertEqual(result["iteration"], 2)

    def test_three_low_quality_searches_end_with_evidence_fallback(self) -> None:
        queries: list[str] = []
        generated = 0

        def retrieve(query: str) -> list[Document]:
            queries.append(query)
            return [Document(page_content="unrelated")]

        def generate(state: dict[str, object]) -> str:
            nonlocal generated
            generated += 1
            return "must not be generated"

        result = self._create_graph(
            intent="knowledge",
            document_retriever=retrieve,
            grader=lambda state: {
                "relevance_score": 0.1,
                "feedback": "No matching runbook",
            },
            rewriter=refine_query,
            generator=generate,
        ).invoke({"user_query": "rare fault"})

        self.assertEqual(
            queries,
            ["rare fault", "rare fault refined", "rare fault refined refined"],
        )
        self.assertEqual(generated, 0)
        self.assertEqual(result["iteration"], 3)
        self.assertEqual(result["relevance_score"], 0.1)
        self.assertEqual(result["grading_feedback"], "No matching runbook")
        self.assertIn("MAX_ITERATIONS_REACHED", result["error"])
        self.assertIn("relevant evidence", result["answer"])

    def test_hybrid_rewrite_refreshes_all_context_sources(self) -> None:
        calls: list[tuple[str, str]] = []
        scores = iter((0.1, 0.9))

        def record(name: str, query: str) -> None:
            calls.append((name, query))

        result = self._create_graph(
            intent="hybrid",
            document_retriever=lambda query: (
                record("documents", query) or [Document(page_content=query)]
            ),
            topology_retriever=lambda query: (
                record("topology", query) or {"query": query}
            ),
            metrics_retriever=lambda query: (
                record("metrics", query) or {"query": query}
            ),
            grader=lambda state: {
                "relevance_score": next(scores),
                "feedback": "Refine the device scope",
            },
            rewriter=lambda state: "refined hybrid query",
        ).invoke({"user_query": "hybrid query"})

        self.assertEqual(
            calls,
            [
                ("documents", "hybrid query"),
                ("topology", "hybrid query"),
                ("metrics", "hybrid query"),
                ("documents", "refined hybrid query"),
                ("topology", "refined hybrid query"),
                ("metrics", "refined hybrid query"),
            ],
        )
        self.assertEqual(result["topology_context"]["query"], "refined hybrid query")
        self.assertEqual(result["metrics"]["query"], "refined hybrid query")

    def test_retrieval_and_hallucination_corrections_share_three_rounds(self) -> None:
        scores = iter((0.1, 0.9))
        generated: list[tuple[int, str | None]] = []

        def generate(state: dict[str, object]) -> str:
            generated.append((state["iteration"], state["checker_feedback"]))
            return f"answer-{len(generated)}"

        result = self._create_graph(
            intent="knowledge",
            document_retriever=lambda query: [Document(page_content=query)],
            grader=lambda state: {
                "relevance_score": next(scores),
                "feedback": "Improve evidence",
            },
            rewriter=lambda state: "rewritten once",
            generator=generate,
            checker=lambda state: {
                "approved": False,
                "feedback": "Answer is not grounded",
            },
        ).invoke({"user_query": "original"})

        self.assertEqual(
            generated,
            [(2, None), (2, "Answer is not grounded")],
        )
        self.assertEqual(result["iteration"], 3)
        self.assertEqual(result["answer"], "answer-2")
        self.assertIn("MAX_ITERATIONS_REACHED", result["error"])

    def test_checker_feedback_loops_back_to_generator_until_approved(self) -> None:
        generator_feedback: list[str | None] = []

        def generate(state: dict[str, object]) -> str:
            generator_feedback.append(state["checker_feedback"])
            return f"answer-{len(generator_feedback)}"

        def check(state: dict[str, object]) -> dict[str, object]:
            if state["iteration"] == 1:
                return {"approved": False, "feedback": "Add device evidence"}
            return {"approved": True, "feedback": ""}

        result = self._create_graph(generator=generate, checker=check).invoke(
            {"user_query": "diagnose device"}
        )

        self.assertEqual(generator_feedback, [None, "Add device evidence"])
        self.assertEqual(result["answer"], "answer-2")
        self.assertEqual(result["iteration"], 2)
        self.assertIsNone(result["checker_feedback"])
        self.assertIsNone(result["error"])

    def test_checker_stops_after_maximum_iterations_and_keeps_last_answer(self) -> None:
        answers: list[str] = []

        def generate(state: dict[str, object]) -> str:
            answer = f"answer-{len(answers) + 1}"
            answers.append(answer)
            return answer

        result = self._create_graph(
            generator=generate,
            checker=lambda state: {
                "approved": False,
                "feedback": "Still incomplete",
            },
        ).invoke({"user_query": "complex question"})

        self.assertEqual(answers, ["answer-1", "answer-2", "answer-3"])
        self.assertEqual(result["answer"], "answer-3")
        self.assertEqual(result["iteration"], 3)
        self.assertEqual(result["checker_feedback"], "Still incomplete")
        self.assertIn("MAX_ITERATIONS_REACHED", result["error"])

    def test_retries_transient_errors_and_can_recover(self) -> None:
        attempts = 0

        def retrieve(query: str) -> list[Document]:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise TimeoutError("temporary timeout")
            return [Document(page_content="recovered")]

        result = self._create_graph(
            intent="knowledge",
            document_retriever=retrieve,
        ).invoke({"user_query": "find document"})

        self.assertEqual(attempts, 3)
        self.assertEqual(result["documents"][0].page_content, "recovered")
        self.assertIsNone(result["error"])

    def test_exhausted_retries_return_a_structured_terminal_error(self) -> None:
        attempts = 0

        def generate(state: dict[str, object]) -> str:
            nonlocal attempts
            attempts += 1
            raise ConnectionError("model unavailable")

        result = self._create_graph(generator=generate).invoke(
            {"user_query": "answer me"}
        )

        self.assertEqual(attempts, 3)
        self.assertIn("Generator", result["error"])
        self.assertIn("ConnectionError", result["error"])
        self.assertEqual(result["answer"], "Unable to complete the request after retries.")

    def test_non_transient_and_contract_errors_do_not_retry(self) -> None:
        attempts = 0

        def empty_answer(state: dict[str, object]) -> str:
            nonlocal attempts
            attempts += 1
            return "  "

        result = self._create_graph(generator=empty_answer).invoke(
            {"user_query": "answer me"}
        )
        self.assertEqual(attempts, 1)
        self.assertIn("ValueError", result["error"])

        cases = (
            (
                self._create_graph(intent="unsupported"),
                {"user_query": "question"},
                "intent",
            ),
            (
                self._create_graph(intent="knowledge"),
                {"user_query": "question"},
                "retrieve_documents",
            ),
            (
                self._create_graph(
                    checker=lambda state: {"approved": False, "feedback": " "}
                ),
                {"user_query": "question"},
                "feedback",
            ),
            (self._create_graph(), {"user_query": "  "}, "user_query"),
        )
        for graph, invocation, message in cases:
            with self.subTest(message=message):
                failed = graph.invoke(invocation)
                self.assertIn(message, failed["error"])
                self.assertTrue(failed["answer"])

    def test_quality_callbacks_validate_missing_and_invalid_results(self) -> None:
        documents = lambda query: [Document(page_content="evidence")]
        cases = (
            (
                self._create_graph(
                    intent="knowledge",
                    document_retriever=documents,
                    grader=None,
                ),
                "grade_documents",
            ),
            (
                self._create_graph(
                    intent="knowledge",
                    document_retriever=documents,
                    grader=lambda state: {
                        "relevance_score": 0.1,
                        "feedback": "Needs detail",
                    },
                    rewriter=None,
                ),
                "rewrite_query",
            ),
            (
                self._create_graph(
                    intent="knowledge",
                    document_retriever=documents,
                    grader=lambda state: {
                        "relevance_score": True,
                        "feedback": "Invalid score",
                    },
                ),
                "relevance_score",
            ),
            (
                self._create_graph(
                    intent="knowledge",
                    document_retriever=documents,
                    grader=lambda state: {
                        "relevance_score": 1.1,
                        "feedback": "Invalid score",
                    },
                ),
                "relevance_score",
            ),
            (
                self._create_graph(
                    intent="knowledge",
                    document_retriever=documents,
                    grader=lambda state: {
                        "relevance_score": 0.1,
                        "feedback": " ",
                    },
                ),
                "feedback",
            ),
            (
                self._create_graph(
                    intent="knowledge",
                    document_retriever=documents,
                    grader=lambda state: {
                        "relevance_score": 0.1,
                        "feedback": "Needs detail",
                    },
                    rewriter=lambda state: " ",
                ),
                "rewrite_query",
            ),
            (
                self._create_graph(
                    intent="knowledge",
                    document_retriever=documents,
                    grader=lambda state: {
                        "relevance_score": 0.1,
                        "feedback": "Needs detail",
                    },
                    rewriter=lambda state: " SAME QUERY ",
                ),
                "different",
            ),
        )

        for graph, message in cases:
            with self.subTest(message=message):
                result = graph.invoke({"user_query": "same query"})
                self.assertIn(message, result["error"])
                self.assertTrue(result["answer"])

    def test_new_quality_nodes_retry_transient_errors(self) -> None:
        grade_attempts = 0
        rewrite_attempts = 0
        grades = iter((0.1, 0.9))

        def grade(state: dict[str, object]) -> dict[str, object]:
            nonlocal grade_attempts
            grade_attempts += 1
            if grade_attempts < 3:
                raise TimeoutError("grader timeout")
            return {
                "relevance_score": next(grades),
                "feedback": "Refine evidence",
            }

        def rewrite(state: dict[str, object]) -> str:
            nonlocal rewrite_attempts
            rewrite_attempts += 1
            if rewrite_attempts < 3:
                raise ConnectionError("rewriter unavailable")
            return "rewritten query"

        result = self._create_graph(
            intent="knowledge",
            document_retriever=lambda query: [Document(page_content=query)],
            grader=grade,
            rewriter=rewrite,
        ).invoke({"user_query": "original query"})

        self.assertEqual(grade_attempts, 4)
        self.assertEqual(rewrite_attempts, 3)
        self.assertEqual(result["rewritten_query"], "rewritten query")
        self.assertEqual(result["relevance_score"], 0.9)
        self.assertIsNone(result["error"])

    def test_validates_workflow_limits(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_iterations"):
            self._create_graph(max_iterations=0)
        with self.assertRaisesRegex(ValueError, "max_iterations"):
            self._create_graph(max_iterations=4)
        with self.assertRaisesRegex(ValueError, "max_retry_attempts"):
            self._create_graph(max_retry_attempts=0)
        with self.assertRaisesRegex(ValueError, "max_retry_attempts"):
            self._create_graph(max_retry_attempts=4)
        for threshold in (-0.1, 1.1, True, "0.8"):
            with self.subTest(threshold=threshold):
                with self.assertRaisesRegex(ValueError, "relevance_threshold"):
                    self._create_graph(relevance_threshold=threshold)


if __name__ == "__main__":
    unittest.main()
