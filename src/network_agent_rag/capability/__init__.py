"""Capability discovery layer: versioned capabilities with backend references."""

from network_agent_rag.capability.models import Capability, CapabilityType
from network_agent_rag.capability.registry import CapabilityRegistry, Handler
from network_agent_rag.capability.resolver import CapabilityQuery, CapabilityResolver

__all__ = [
    "Capability",
    "CapabilityQuery",
    "CapabilityRegistry",
    "CapabilityResolver",
    "CapabilityType",
    "Handler",
]
