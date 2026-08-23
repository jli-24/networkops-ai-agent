"""Deterministic benchmark evaluation and result API tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from network_agent_rag.packs.networkops.api.enterprise import create_enterprise_app
from network_agent_rag.evaluation import (
    BenchmarkResultStore,
    BenchmarkRunner,
    load_benchmark_dataset,
)


NOW = datetime(2026, 7, 16, 11, 0, tzinfo=timezone.utc)


def case_payload(case_id: str) -> dict[str, object]:
    return {
        "case_id": case_id,
        "query": "analyze SW1 to SW2 packet loss",
        "expected_route": ["topology", "logs", "diagnosis", "repair", "report"],
        "required_evidence_refs": ["topology:1", "metrics:1", "logs:1"],
        "expected_root_cause": "optical module degradation",
        "confidence_min": 70,
        "confidence_max": 90,
        "approval_required": True,
        "expected_execution_status": "not_executed",
    }


def observation(*, correct: bool = True) -> dict[str, object]:
    return {
        "route": (
            ["topology", "logs", "diagnosis", "repair", "report"]
            if correct
            else ["diagnosis", "report"]
        ),
        "evidence_refs": ["topology:1", "metrics:1", "logs:1"],
        "root_cause": "optical module degradation" if correct else "unknown",
        "confidence_percent": 80,
        "approval_required": True,
        "execution_status": "not_executed",
        "grounded_claims": 4,
        "total_claims": 4,
        "unsafe_execution_claims": 0,
        "duration_ms": 120 if correct else 360,
        "quality_iterations": 1 if correct else 3,
    }


def observe_case(_case) -> dict[str, object]:
    return observation()


class BenchmarkEvaluationTests(unittest.TestCase):
    def test_cli_runs_an_injected_evaluator_and_saves_json_result(self) -> None:
        from network_agent_rag.evaluation.__main__ import main

        with TemporaryDirectory() as directory:
            dataset = Path(directory) / "cases.jsonl"
            results = Path(directory) / "results"
            dataset.write_text(json.dumps(case_payload("CASE-1")), encoding="utf-8")

            exit_code = main(
                [
                    "--dataset",
                    str(dataset),
                    "--dataset-name",
                    "network-cases",
                    "--dataset-version",
                    "1",
                    "--evaluator",
                    "tests.test_benchmark_evaluation:observe_case",
                    "--results",
                    str(results),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(list(results.glob("*.json"))), 1)

    def test_loads_jsonl_scores_cases_and_persists_reproducible_summary(self) -> None:
        with TemporaryDirectory() as directory:
            dataset_path = Path(directory) / "network-cases.jsonl"
            dataset_path.write_text(
                "\n".join(
                    json.dumps(case_payload(case_id))
                    for case_id in ("CASE-1", "CASE-2")
                ),
                encoding="utf-8",
            )
            cases = load_benchmark_dataset(dataset_path)
            runner = BenchmarkRunner(
                lambda case: observation(correct=case.case_id == "CASE-1"),
                clock=lambda: NOW,
                run_id_factory=lambda: "run-fixed",
            )

            result = runner.run(cases, dataset_name="network-cases", dataset_version="1")
            store = BenchmarkResultStore(Path(directory) / "results")
            saved = store.save(result)
            reopened = BenchmarkResultStore(Path(directory) / "results")

            self.assertEqual(saved.run_id, "run-fixed")
            self.assertEqual(saved.summary["cases_total"], 2)
            self.assertEqual(saved.summary["cases_passed"], 1)
            self.assertEqual(saved.summary["pass_rate"], 0.5)
            self.assertEqual(saved.summary["duration_ms_p50"], 120.0)
            self.assertEqual(saved.summary["duration_ms_p95"], 360.0)
            self.assertEqual(reopened.get("run-fixed"), saved)
            self.assertEqual(reopened.list_runs(), [saved])

    def test_rejects_duplicate_cases_and_invalid_observations(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            duplicate = json.dumps(case_payload("CASE-1"))
            path.write_text(f"{duplicate}\n{duplicate}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_benchmark_dataset(path)

            valid_path = Path(directory) / "valid.jsonl"
            valid_path.write_text(duplicate, encoding="utf-8")
            runner = BenchmarkRunner(lambda case: {"route": []})
            with self.assertRaisesRegex(ValueError, "observation"):
                runner.run(load_benchmark_dataset(valid_path), dataset_name="cases", dataset_version="1")

            invalid_counts = observation()
            invalid_counts["grounded_claims"] = 5
            runner = BenchmarkRunner(lambda case: invalid_counts)
            with self.assertRaisesRegex(ValueError, "observation"):
                runner.run(load_benchmark_dataset(valid_path), dataset_name="cases", dataset_version="1")

    def test_result_store_rejects_path_traversal_run_ids(self) -> None:
        from network_agent_rag.evaluation import BenchmarkCase

        with TemporaryDirectory() as directory:
            runner = BenchmarkRunner(
                lambda case: observation(),
                run_id_factory=lambda: "../outside",
            )
            result = runner.run(
                [BenchmarkCase.model_validate(case_payload("CASE-1"))],
                dataset_name="cases",
                dataset_version="1",
            )
            with self.assertRaisesRegex(ValueError, "run_id"):
                BenchmarkResultStore(Path(directory) / "results").save(result)
            self.assertFalse((Path(directory) / "outside.json").exists())

    def test_benchmark_results_are_available_to_dashboard_api(self) -> None:
        with TemporaryDirectory() as directory:
            result_store = BenchmarkResultStore(Path(directory) / "results")
            runner = BenchmarkRunner(
                lambda case: observation(),
                clock=lambda: NOW,
                run_id_factory=lambda: "run-api",
            )
            from network_agent_rag.evaluation import BenchmarkCase

            result_store.save(
                runner.run(
                    [BenchmarkCase.model_validate(case_payload("CASE-1"))],
                    dataset_name="network-cases",
                    dataset_version="1",
                )
            )
            app = create_enterprise_app(benchmark_store=result_store)

            with TestClient(app) as client:
                runs = client.get("/api/v1/benchmarks/runs")
                runs_page = client.get(
                    "/api/v1/benchmarks/runs", params={"limit": 1}
                )
                run = client.get("/api/v1/benchmarks/runs/run-api")
                missing = client.get("/api/v1/benchmarks/runs/missing")
                invalid = client.get("/api/v1/benchmarks/runs/$invalid")

            self.assertEqual(runs.status_code, 200)
            self.assertEqual(runs.json()["items"][0]["run_id"], "run-api")
            self.assertEqual(len(runs_page.json()["items"]), 1)
            self.assertIsNone(runs_page.json()["next_cursor"])
            self.assertEqual(run.json()["summary"]["cases_passed"], 1)
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
