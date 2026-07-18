"""Stable in-memory reports for offline evaluations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import hashlib
import json

from network_agent_rag.evaluation.metrics import aggregate_evaluation_metrics
from network_agent_rag.evaluation.models import BenchmarkReport, EvaluationResult


def build_benchmark_report(
    results: Sequence[EvaluationResult],
    *,
    dataset_version: str,
    created_at: datetime,
) -> BenchmarkReport:
    ordered = tuple(sorted(results, key=lambda item: item.case_id))
    metrics = aggregate_evaluation_metrics(ordered)
    identity_payload = {
        "dataset_version": dataset_version,
        "metrics": metrics.model_dump(mode="json"),
        "results": [item.model_dump(mode="json") for item in ordered],
    }
    report_id = hashlib.sha256(_canonical_json(identity_payload).encode("utf-8")).hexdigest()
    passed = sum(item.success for item in ordered)
    return BenchmarkReport(
        report_id=report_id,
        dataset_version=dataset_version,
        total_cases=len(ordered),
        passed_cases=passed,
        failed_cases=len(ordered) - passed,
        metrics=metrics,
        results=ordered,
        created_at=created_at,
    )


class EvaluationReportBuilder:
    def to_json(self, report: BenchmarkReport) -> str:
        return _canonical_json(report.model_dump(mode="json"))

    def to_markdown(self, report: BenchmarkReport) -> str:
        lines = [
            f"# NetworkOps AI Agent Evaluation {report.dataset_version}",
            "",
            f"Report ID: `{report.report_id}`",
            "",
            f"- Total cases: {report.total_cases}",
            f"- Passed cases: {report.passed_cases}",
            f"- Failed cases: {report.failed_cases}",
            f"- RCA accuracy: {report.metrics.rca_accuracy:.4f}",
            f"- Execution safety rate: {report.metrics.execution_safety_rate:.4f}",
            "",
            "| Case | Result | Error code |",
            "|---|---|---|",
        ]
        for result in sorted(report.results, key=lambda item: item.case_id):
            lines.append(
                f"| {result.case_id} | {'passed' if result.success else 'failed'} | "
                f"{result.error_code or ''} |"
            )
        return "\n".join(lines) + "\n"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = ["EvaluationReportBuilder", "build_benchmark_report"]
