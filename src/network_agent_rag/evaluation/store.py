"""JSON result store for offline benchmark runs."""

from __future__ import annotations

from pathlib import Path
import json

from network_agent_rag.evaluation.models import BenchmarkRunResult


class BenchmarkResultStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, result: BenchmarkRunResult) -> BenchmarkRunResult:
        path = self.directory / f"{_safe_id(result.run_id)}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)
        return result

    def get(self, run_id: str) -> BenchmarkRunResult:
        path = self.directory / f"{_safe_id(run_id)}.json"
        if not path.exists():
            raise KeyError(f"unknown benchmark run: {run_id}")
        return BenchmarkRunResult.model_validate_json(path.read_text(encoding="utf-8"))

    def list_runs(self) -> list[BenchmarkRunResult]:
        runs = [
            BenchmarkRunResult.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.directory.glob("*.json")
        ]
        return sorted(runs, key=lambda item: (item.finished_at, item.run_id), reverse=True)


def _safe_id(value: str) -> str:
    if not isinstance(value, str) or not value or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        for character in value
    ):
        raise ValueError("run_id contains unsupported characters")
    return value


__all__ = ["BenchmarkResultStore"]
