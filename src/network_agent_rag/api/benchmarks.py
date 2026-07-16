"""Read-only API for offline benchmark results."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from network_agent_rag.evaluation import BenchmarkResultStore


benchmark_router = APIRouter(prefix="/benchmarks")


@benchmark_router.get("/runs")
def list_benchmark_runs(
    request: Request,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    try:
        offset = int(cursor) if cursor is not None else 0
    except ValueError as error:
        raise HTTPException(status_code=422, detail="cursor must be a non-negative integer") from error
    if offset < 0:
        raise HTTPException(status_code=422, detail="cursor must be a non-negative integer")
    runs = _store(request).list_runs()
    items = runs[offset : offset + limit]
    next_offset = offset + len(items)
    return {
        "items": items,
        "next_cursor": str(next_offset) if next_offset < len(runs) else None,
    }


@benchmark_router.get("/runs/{run_id}")
def benchmark_run(run_id: str, request: Request) -> object:
    try:
        return _store(request).get(run_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Benchmark run not found") from error


def _store(request: Request) -> BenchmarkResultStore:
    store = getattr(request.app.state, "benchmark_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Benchmark result store is not configured")
    return store


__all__ = ["benchmark_router"]
