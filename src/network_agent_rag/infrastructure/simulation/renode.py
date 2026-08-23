"""Renode adapter placeholder.

Not implemented in v0.15.0. When Renode support lands, implement the
``SimulatorBackend`` protocol here (drive a ``.resc`` script via Renode's
monitor socket) and register it behind the same capabilities.
"""

from __future__ import annotations


class RenodeBackendNotImplemented(NotImplementedError):
    pass


def create_renode_backend():  # noqa: ANN201 - placeholder
    raise RenodeBackendNotImplemented(
        "Renode adapter is planned for a later release; "
        "use the in_process backend for now"
    )


__all__ = ["RenodeBackendNotImplemented", "create_renode_backend"]
