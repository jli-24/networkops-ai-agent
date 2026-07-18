"""Independent metrics aggregation for offline evaluations."""

from __future__ import annotations

from collections.abc import Sequence

from network_agent_rag.evaluation.models import (
    EvaluationMetricsSnapshot,
    EvaluationResult,
)


def aggregate_evaluation_metrics(
    results: Sequence[EvaluationResult],
    *,
    benchmark_count: int | None = None,
) -> EvaluationMetricsSnapshot:
    items = tuple(results)
    total = len(items)
    if not items:
        return EvaluationMetricsSnapshot(
            benchmark_count=0 if benchmark_count is None else benchmark_count,
            total_cases=0,
            rca_accuracy=0.0,
            top1_accuracy=0.0,
            top3_accuracy=0.0,
            confidence_alignment=0.0,
            retrieval_hit_rate=0.0,
            context_precision=0.0,
            context_recall=0.0,
            repair_success_rate=0.0,
            policy_block_rate=0.0,
            blocked_rate=0.0,
            approval_rate=0.0,
            execution_safety_rate=0.0,
            avg_latency=0.0,
            p50_latency=0.0,
            p95_latency=0.0,
            avg_tool_calls=0.0,
            failed_tool_calls=0,
            duplicate_calls=0,
        )

    execution_items = tuple(item for item in items if item.execution_expected)
    execution_total = len(execution_items)
    latencies = sorted(item.latency for item in items)

    def average(attribute: str) -> float:
        return round(
            sum(float(getattr(item, attribute)) for item in items) / total,
            4,
        )

    def execution_rate(attribute: str) -> float:
        if not execution_items:
            return 0.0
        return round(
            sum(bool(getattr(item, attribute)) for item in execution_items)
            / execution_total,
            4,
        )

    return EvaluationMetricsSnapshot(
        benchmark_count=1 if benchmark_count is None else benchmark_count,
        total_cases=total,
        rca_accuracy=average("rca_match"),
        top1_accuracy=average("top1_accuracy"),
        top3_accuracy=average("top3_accuracy"),
        confidence_alignment=average("confidence_alignment"),
        retrieval_hit_rate=average("retrieval_hit_rate"),
        context_precision=average("context_precision"),
        context_recall=average("context_recall"),
        repair_success_rate=execution_rate("repair_success"),
        policy_block_rate=execution_rate("policy_blocked"),
        blocked_rate=execution_rate("blocked"),
        approval_rate=execution_rate("approval_required"),
        execution_safety_rate=average("execution_safety"),
        avg_latency=round(sum(latencies) / total, 3),
        p50_latency=_linear_percentile(latencies, 0.50),
        p95_latency=_linear_percentile(latencies, 0.95),
        avg_tool_calls=round(sum(item.tool_calls for item in items) / total, 3),
        failed_tool_calls=sum(item.failed_tool_calls for item in items),
        duplicate_calls=sum(item.duplicate_tools for item in items),
    )


def _linear_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    rank = (len(values) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    fraction = rank - lower
    return round(values[lower] + (values[upper] - values[lower]) * fraction, 3)


__all__ = ["aggregate_evaluation_metrics"]
