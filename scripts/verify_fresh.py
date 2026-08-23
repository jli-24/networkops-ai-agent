"""verify-fresh (L1): zero-state boot proof for every step (iron law 28).

Redirects ALL data roots (artifacts / chroma / checkpoints) to a temp
directory, boots the app through the pack pipeline (registration-time
knowledge initialization included), runs an API smoke end to end, then
the embedded-demo recipe. Offline: deterministic embeddings, no HF
downloads. L2 (fresh git clone) runs this same script before tagging.

Exit code 0 = fresh boot verified; any failure exits non-zero.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.embeddings import Embeddings


class DeterministicEmbeddings(Embeddings):
    """Keyword-count embeddings; quality is irrelevant for smoke tests."""

    _KEYWORDS = ("esp32", "spi", "i2c", "mcu", "freertos", "wifi")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        lowered = text.casefold()
        return [float(lowered.count(k)) for k in self._KEYWORDS] + [1.0]


def main() -> int:
    from network_agent_rag.main import create_app
    from network_agent_rag.packs.embeddedops.api import EmbeddedServices
    from network_agent_rag.packs.networkops.demo.multi_agent_demo import (
        create_multi_agent_demo_app,
    )

    tmp = tempfile.mkdtemp(prefix="agentos-verify-fresh-")
    root = Path(tmp)
    try:
        print(f"[verify-fresh] data root -> {root}")
        services = EmbeddedServices(
            artifact_root=str(root / "artifacts"),
            knowledge_persist_directory=str(root / "chroma"),
            knowledge_embeddings=DeterministicEmbeddings(),
        )
        client = TestClient(create_app(embedded_services=services))
        packs = services.pack_registry.list(enabled=True)
        pack_names = sorted(p.name for p in packs)
        print(f"[verify-fresh] enabled packs: {pack_names}")
        assert pack_names == ["embeddedops", "networkops"], "expected both packs"

        health = client.get("/api/v1/health")
        print(f"[verify-fresh] GET /api/v1/health -> {health.status_code}")
        assert health.status_code == 200

        capabilities = client.get("/api/v1/capabilities")
        names = [item["name"] for item in capabilities.json()]
        print(f"[verify-fresh] GET /api/v1/capabilities -> {capabilities.status_code} {len(names)} caps")
        assert capabilities.status_code == 200 and "esp32_compile" in names

        created = client.post(
            "/api/v1/embedded/tasks", json={"goal": "ESP32 温湿度采集节点并验证"}
        )
        body = created.json()
        print(f"[verify-fresh] POST /api/v1/embedded/tasks -> {created.status_code} {body['status']}")
        assert created.status_code == 201 and body["status"] == "AWAITING_APPROVAL"

        decided = client.post(
            f"/api/v1/embedded/tasks/{body['task_id']}/approval",
            json={"decision": "approve", "actor": "verify-fresh"},
        )
        final = decided.json()
        print(
            f"[verify-fresh] POST .../approval -> {decided.status_code} "
            f"{final['status']}/{final['validation_state']}"
        )
        assert decided.status_code == 200
        assert final["status"] == "COMPLETED"
        assert final["validation_state"] == "PASSED"

        artifacts = client.get(f"/api/v1/embedded/tasks/{body['task_id']}").json()
        artifact_names = [item["name"] for item in artifacts["artifacts"]]
        print(f"[verify-fresh] artifacts: {artifact_names}")
        assert "hardware_design.json" in artifact_names and "main.c" in artifact_names

        # Network pack end-to-end: deterministic demo workflow through the
        # same platform assembly, streamed over the chat SSE surface.
        network_app = create_multi_agent_demo_app(
            persist_directory=str(root / "network-chroma"),
            embeddings=DeterministicEmbeddings(),
        )
        network_client = TestClient(network_app)
        with network_client.stream(
            "POST",
            "/api/v1/chat",
            json={"query": "分析 SW1 到 SW2 丢包", "session_id": "verify-fresh"},
        ) as response:
            print(f"[verify-fresh] POST /api/v1/chat -> {response.status_code}")
            assert response.status_code == 200
            events = []
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8")
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
        print(f"[verify-fresh] chat SSE events: {events[:6]}...")
        assert "start" in events and "answer" in events

        print(
            "[verify-fresh] demo recipe: capabilities =",
            [c.key for c in services.capability_registry.list()],
        )
        print("[verify-fresh] OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
