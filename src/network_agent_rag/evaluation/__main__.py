"""Run deterministic benchmark datasets with an injected evaluator callback."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import argparse
from importlib import import_module

from network_agent_rag.evaluation import (
    BenchmarkResultStore,
    BenchmarkRunner,
    load_benchmark_dataset,
)
from network_agent_rag.evaluation.models import BenchmarkCase


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run NetworkOps benchmark cases")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--dataset-name", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--evaluator", required=True, help="Python callback as module:function")
    parser.add_argument("--results", required=True)
    options = parser.parse_args(arguments)
    callback = _load_callback(options.evaluator)
    result = BenchmarkRunner(callback).run(
        load_benchmark_dataset(options.dataset),
        dataset_name=options.dataset_name,
        dataset_version=options.dataset_version,
    )
    BenchmarkResultStore(options.results).save(result)
    print(result.run_id)
    return 0


def _load_callback(value: str) -> Callable[[BenchmarkCase], object]:
    module_name, separator, attribute = value.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("evaluator must use module:function format")
    callback = getattr(import_module(module_name), attribute)
    if not callable(callback):
        raise TypeError("evaluator must be callable")
    return callback


if __name__ == "__main__":
    raise SystemExit(main())
