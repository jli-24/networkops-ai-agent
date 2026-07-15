"""Deterministic monitoring tools for LangGraph agent workflows."""

from __future__ import annotations

from langchain_core.tools import tool


_OBSERVED_AT = "2026-07-12T09:00:00+08:00"
_SEVERITIES = {"critical", "major", "minor", "warning"}

_DEVICES: dict[str, dict[str, object]] = {
    "sw1": {
        "device_id": "SW1",
        "device_name": "Distribution Switch SW1",
        "management_ip": "10.20.0.11",
        "status": "degraded",
        "cpu_percent": 41.2,
        "memory_percent": 63.4,
        "traffic_in_mbps": 532.8,
        "traffic_out_mbps": 487.3,
        "packet_loss_percent": 3.8,
        "interfaces": [
            {
                "interface_name": "Gi0/1",
                "description": "Physical link to SW2 Gi0/24",
                "admin_status": "up",
                "oper_status": "up",
                "traffic_in_mbps": 532.8,
                "traffic_out_mbps": 487.3,
                "packet_loss_percent": 3.8,
                "crc_errors": 1250,
                "crc_errors_delta": 320,
                "input_errors": 1300,
                "input_errors_delta": 340,
                "optical_rx_dbm": -19.8,
                "optical_rx_low_threshold_dbm": -18.0,
            }
        ],
    },
    "sw2": {
        "device_id": "SW2",
        "device_name": "Access Switch SW2",
        "management_ip": "10.20.0.12",
        "status": "degraded",
        "cpu_percent": 36.7,
        "memory_percent": 57.9,
        "traffic_in_mbps": 486.1,
        "traffic_out_mbps": 531.5,
        "packet_loss_percent": 3.6,
        "interfaces": [
            {
                "interface_name": "Gi0/24",
                "description": "Physical link to SW1 Gi0/1",
                "admin_status": "up",
                "oper_status": "up",
                "traffic_in_mbps": 486.1,
                "traffic_out_mbps": 531.5,
                "packet_loss_percent": 3.6,
                "crc_errors": 1198,
                "crc_errors_delta": 301,
                "input_errors": 1240,
                "input_errors_delta": 326,
                "optical_rx_dbm": -20.1,
                "optical_rx_low_threshold_dbm": -18.0,
            }
        ],
    },
    "core-sw-01": {
        "device_id": "core-sw-01",
        "device_name": "Core Switch 01",
        "management_ip": "10.10.0.11",
        "status": "online",
        "cpu_percent": 32.5,
        "memory_percent": 58.2,
        "traffic_in_mbps": 810.4,
        "traffic_out_mbps": 625.8,
        "packet_loss_percent": 0.05,
        "interfaces": [
            {
                "interface_name": "GigabitEthernet1/0/1",
                "description": "Uplink to edge-rtr-01",
                "admin_status": "up",
                "oper_status": "up",
                "traffic_in_mbps": 420.2,
                "traffic_out_mbps": 315.6,
                "packet_loss_percent": 0.03,
            },
            {
                "interface_name": "GigabitEthernet1/0/2",
                "description": "Server aggregation link",
                "admin_status": "up",
                "oper_status": "up",
                "traffic_in_mbps": 390.2,
                "traffic_out_mbps": 310.2,
                "packet_loss_percent": 0.07,
            },
            {
                "interface_name": "Loopback0",
                "description": "Routing identifier",
                "admin_status": "up",
                "oper_status": "up",
                "traffic_in_mbps": 0.0,
                "traffic_out_mbps": 0.0,
                "packet_loss_percent": 0.0,
            },
        ],
    },
    "edge-rtr-01": {
        "device_id": "edge-rtr-01",
        "device_name": "Edge Router 01",
        "management_ip": "10.10.0.1",
        "status": "degraded",
        "cpu_percent": 78.4,
        "memory_percent": 72.1,
        "traffic_in_mbps": 420.6,
        "traffic_out_mbps": 390.2,
        "packet_loss_percent": 2.4,
        "interfaces": [
            {
                "interface_name": "GigabitEthernet0/0",
                "description": "WAN uplink",
                "admin_status": "up",
                "oper_status": "up",
                "traffic_in_mbps": 420.6,
                "traffic_out_mbps": 390.2,
                "packet_loss_percent": 2.4,
            },
            {
                "interface_name": "GigabitEthernet0/1",
                "description": "Backup WAN uplink",
                "admin_status": "up",
                "oper_status": "down",
                "traffic_in_mbps": 0.0,
                "traffic_out_mbps": 0.0,
                "packet_loss_percent": 100.0,
            },
        ],
    },
    "access-sw-01": {
        "device_id": "access-sw-01",
        "device_name": "Access Switch 01",
        "management_ip": "10.10.1.21",
        "status": "offline",
        "cpu_percent": 0.0,
        "memory_percent": 0.0,
        "traffic_in_mbps": 0.0,
        "traffic_out_mbps": 0.0,
        "packet_loss_percent": 100.0,
        "interfaces": [
            {
                "interface_name": "GigabitEthernet1/0/1",
                "description": "User access port",
                "admin_status": "up",
                "oper_status": "down",
                "traffic_in_mbps": 0.0,
                "traffic_out_mbps": 0.0,
                "packet_loss_percent": 100.0,
            },
            {
                "interface_name": "GigabitEthernet1/0/24",
                "description": "Distribution uplink",
                "admin_status": "up",
                "oper_status": "down",
                "traffic_in_mbps": 0.0,
                "traffic_out_mbps": 0.0,
                "packet_loss_percent": 100.0,
            },
        ],
    },
}

_ALARMS = [
    {
        "alarm_id": "ALM-1001",
        "device_id": "edge-rtr-01",
        "interface_name": "GigabitEthernet0/1",
        "severity": "critical",
        "status": "active",
        "category": "interface",
        "message": "Backup WAN interface is down",
        "first_seen": "2026-07-12T08:42:00+08:00",
        "last_seen": _OBSERVED_AT,
    },
    {
        "alarm_id": "ALM-1002",
        "device_id": "core-sw-01",
        "interface_name": None,
        "severity": "minor",
        "status": "active",
        "category": "resource",
        "message": "Memory utilization exceeded the warning threshold",
        "first_seen": "2026-07-12T08:50:00+08:00",
        "last_seen": _OBSERVED_AT,
    },
    {
        "alarm_id": "ALM-0998",
        "device_id": "core-sw-01",
        "interface_name": "GigabitEthernet1/0/2",
        "severity": "warning",
        "status": "cleared",
        "category": "packet_loss",
        "message": "Transient packet loss returned to normal",
        "first_seen": "2026-07-12T07:30:00+08:00",
        "last_seen": "2026-07-12T07:36:00+08:00",
    },
]


def _error(error_code: str, message: str) -> dict[str, object]:
    return {"ok": False, "error_code": error_code, "message": message}


def _find_device(device_id: str) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    normalized = device_id.strip().casefold()
    if not normalized:
        return None, _error("INVALID_ARGUMENT", "device_id must not be blank")
    device = _DEVICES.get(normalized)
    if device is None:
        return None, _error("DEVICE_NOT_FOUND", f"Unknown device: {device_id.strip()}")
    return device, None


@tool
def query_device_status(device_id: str) -> dict[str, object]:
    """Query CPU, memory, traffic, packet loss, and interface status for a device."""
    device, error = _find_device(device_id)
    if error is not None:
        return error
    assert device is not None

    interfaces = device["interfaces"]
    assert isinstance(interfaces, list)
    up_count = sum(interface["oper_status"] == "up" for interface in interfaces)
    return {
        "ok": True,
        **{key: value for key, value in device.items() if key != "interfaces"},
        "interfaces": {
            "total": len(interfaces),
            "up": up_count,
            "down": len(interfaces) - up_count,
        },
        "observed_at": _OBSERVED_AT,
    }


@tool
def query_interface(
    device_id: str,
    interface_name: str | None = None,
) -> dict[str, object]:
    """Query one interface or all interfaces on a network device."""
    device, error = _find_device(device_id)
    if error is not None:
        return error
    assert device is not None

    interfaces = device["interfaces"]
    assert isinstance(interfaces, list)
    selected = interfaces
    if interface_name is not None:
        normalized_name = interface_name.strip().casefold()
        if not normalized_name:
            return _error("INVALID_ARGUMENT", "interface_name must not be blank")
        selected = [
            interface
            for interface in interfaces
            if str(interface["interface_name"]).casefold() == normalized_name
        ]
        if not selected:
            return _error(
                "INTERFACE_NOT_FOUND",
                f"Unknown interface on {device['device_id']}: {interface_name.strip()}",
            )

    return {
        "ok": True,
        "device_id": device["device_id"],
        "count": len(selected),
        "interfaces": [dict(interface) for interface in selected],
        "observed_at": _OBSERVED_AT,
    }


@tool
def query_alarm(
    device_id: str | None = None,
    severity: str | None = None,
    active_only: bool = True,
) -> dict[str, object]:
    """Query alarms, optionally filtering by device, severity, and active status."""
    canonical_device_id: str | None = None
    if device_id is not None:
        device, error = _find_device(device_id)
        if error is not None:
            return error
        assert device is not None
        canonical_device_id = str(device["device_id"])

    canonical_severity: str | None = None
    if severity is not None:
        canonical_severity = severity.strip().casefold()
        if canonical_severity not in _SEVERITIES:
            allowed = ", ".join(sorted(_SEVERITIES))
            return _error("INVALID_ARGUMENT", f"severity must be one of: {allowed}")

    alarms = [
        dict(alarm)
        for alarm in _ALARMS
        if (canonical_device_id is None or alarm["device_id"] == canonical_device_id)
        and (canonical_severity is None or alarm["severity"] == canonical_severity)
        and (not active_only or alarm["status"] == "active")
    ]
    return {
        "ok": True,
        "count": len(alarms),
        "alarms": alarms,
        "observed_at": _OBSERVED_AT,
    }


MONITORING_TOOLS = [query_device_status, query_interface, query_alarm]

__all__ = [
    "MONITORING_TOOLS",
    "query_alarm",
    "query_device_status",
    "query_interface",
]
