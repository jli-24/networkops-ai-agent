"""Artifact storage: protocol plus a manifest-backed filesystem store."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from network_agent_rag.artifact.models import Artifact, ArtifactType


def compute_sha256(content: str | bytes) -> str:
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


class ArtifactStore(Protocol):
    """Persistence boundary for task artifacts."""

    def save(
        self,
        *,
        task_id: str,
        type: ArtifactType,
        name: str,
        version: str,
        created_by: str,
        content: str | bytes,
    ) -> Artifact: ...

    def list(self, task_id: str) -> list[Artifact]: ...

    def read(self, artifact: Artifact) -> bytes: ...

    def verify_integrity(self, artifact: Artifact) -> bool: ...


class FileSystemArtifactStore:
    """Stores artifacts under ``<root>/<task_id>/`` with a manifest.

    Layout::

        data/artifacts/emb-000001/main.c
        data/artifacts/emb-000001/compile.log
        data/artifacts/emb-000001/manifest.json

    The manifest records metadata (including the sha256) for listing and
    integrity verification; file contents can always be re-hashed.
    """

    _MANIFEST = "manifest.json"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        *,
        task_id: str,
        type: ArtifactType,
        name: str,
        version: str,
        created_by: str,
        content: str | bytes,
    ) -> Artifact:
        if not task_id.strip():
            raise ValueError("task_id must not be blank")
        data = content.encode("utf-8") if isinstance(content, str) else content
        task_dir = self._root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        target = task_dir / name
        if target.exists():
            raise FileExistsError(f"artifact already exists: {target}")
        target.write_bytes(data)
        artifact = Artifact(
            artifact_id=f"art-{uuid4().hex[:12]}",
            task_id=task_id,
            type=type,
            name=name,
            path=str(target.relative_to(self._root)),
            version=version,
            created_by=created_by,
            sha256=compute_sha256(data),
            size_bytes=len(data),
        )
        manifest = self._read_manifest(task_id)
        manifest.append(artifact.model_dump(mode="json"))
        self._write_manifest(task_id, manifest)
        return artifact

    def list(self, task_id: str) -> list[Artifact]:
        manifest = self._read_manifest(task_id)
        return [Artifact.model_validate(entry) for entry in manifest]

    def read(self, artifact: Artifact) -> bytes:
        path = self._root / artifact.path
        if not path.is_file():
            raise FileNotFoundError(f"artifact content missing: {path}")
        return path.read_bytes()

    def verify_integrity(self, artifact: Artifact) -> bool:
        try:
            data = self.read(artifact)
        except FileNotFoundError:
            return False
        return compute_sha256(data) == artifact.sha256

    def _read_manifest(self, task_id: str) -> list[dict[str, str]]:
        path = self._root / task_id / self._MANIFEST
        if not path.is_file():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_manifest(self, task_id: str, manifest: list[dict[str, str]]) -> None:
        path = self._root / task_id / self._MANIFEST
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


__all__ = ["ArtifactStore", "FileSystemArtifactStore", "compute_sha256"]
