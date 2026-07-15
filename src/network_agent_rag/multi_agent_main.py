"""Explicit FastAPI entry point for the v0.2 multi-agent demonstration."""

from network_agent_rag.multi_agent_demo import create_multi_agent_demo_app


app = create_multi_agent_demo_app()
