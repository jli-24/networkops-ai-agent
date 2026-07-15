"""ASGI entry point for the explicit SW1-SW2 diagnosis demonstration."""

from network_agent_rag.demo import create_demo_app


app = create_demo_app()
