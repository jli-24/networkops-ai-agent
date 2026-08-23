"""Wokwi adapter placeholder.

Not implemented in v0.15.0. When Wokwi CLI support lands, implement the
``SimulatorBackend`` protocol here (launch ``wokwi-cli`` against a
``diagram.json`` + ``firmware.elf``) and register it as an additional
backend behind the same capabilities; agents discover the swap through the
capability registry, not through code changes.
"""

from __future__ import annotations


class WokwiBackendNotImplemented(NotImplementedError):
    pass


def create_wokwi_backend():  # noqa: ANN201 - placeholder
    raise WokwiBackendNotImplemented(
        "Wokwi adapter is planned for a later release; "
        "use the in_process backend for now"
    )


__all__ = ["WokwiBackendNotImplemented", "create_wokwi_backend"]
