"""Cross-platform launcher for the local NetworkOps showcase."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import IO, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from network_agent_rag.core.config import Settings


ROOT = Path(__file__).resolve().parents[1]
CONSOLE_DIR = ROOT / "console"
START_TIMEOUT_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.25
SHUTDOWN_TIMEOUT_SECONDS = 5.0
WINDOWS_CREATE_SUSPENDED = 0x00000004
DEFAULT_WORKFLOW_FACTORY = "demo.networkops_local_factory:create_workflow"
LEGACY_LOCAL_WORKFLOW_FACTORY = "networkops_local_factory:create_workflow"


class DemoLaunchError(RuntimeError):
    """A safe, user-facing launcher failure."""


class Process(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def send_signal(self, sig: int) -> None: ...


@dataclass(frozen=True)
class Service:
    name: str
    host: str
    port: int
    health_url: str


API_SERVICE = Service(
    name="Enterprise API",
    host="127.0.0.1",
    port=8020,
    health_url="http://127.0.0.1:8020/health",
)
CONSOLE_SERVICE = Service(
    name="Operator Console",
    host="127.0.0.1",
    port=5173,
    health_url="http://127.0.0.1:5173/",
)


def _api_command(python: str) -> tuple[str, ...]:
    return (
        python,
        "-m",
        "uvicorn",
        "deployment.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        "8020",
    )


def _console_command(npm: str) -> tuple[str, ...]:
    return (
        npm,
        "run",
        "dev",
        "--",
        "--host",
        "127.0.0.1",
        "--port",
        "5173",
    )


def _find_npm(platform_name: str | None = None) -> str:
    command = "npm.cmd" if (platform_name or os.name) == "nt" else "npm"
    if shutil.which(command) is None:
        raise DemoLaunchError("Node.js npm executable was not found")
    return command


def _validate_settings(settings: object) -> None:
    """Validate only presence; secret values must never be read."""
    if getattr(settings, "jwt_secret_key", None) is None:
        raise DemoLaunchError("JWT_SECRET_KEY is not configured")


def _api_environment(settings: object) -> dict[str, str]:
    environment = dict(os.environ)
    configured = getattr(settings, "networkops_workflow_factory", None)
    environment["NETWORKOPS_WORKFLOW_FACTORY"] = (
        DEFAULT_WORKFLOW_FACTORY
        if not configured or configured == LEGACY_LOCAL_WORKFLOW_FACTORY
        else str(configured)
    )
    return environment


def _is_healthy(service: Service) -> bool:
    try:
        with urlopen(service.health_url, timeout=1.0) as response:  # noqa: S310
            return 200 <= response.status < 400
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def _port_in_use(service: Service) -> bool:
    try:
        with socket.create_connection((service.host, service.port), timeout=0.5):
            return True
    except OSError:
        return False


def _create_windows_job(process: subprocess.Popen[bytes]) -> int:
    """Assign a child tree to a kill-on-close Windows Job Object."""
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise DemoLaunchError("Could not create a managed Windows process tree")

    information = EXTENDED_LIMIT_INFORMATION()
    information.BasicLimitInformation.LimitFlags = 0x00002000
    configured = kernel32.SetInformationJobObject(
        handle,
        9,
        ctypes.byref(information),
        ctypes.sizeof(information),
    )
    assigned = configured and kernel32.AssignProcessToJobObject(
        handle,
        wintypes.HANDLE(process._handle),  # type: ignore[attr-defined]
    )
    if not assigned:
        kernel32.CloseHandle(handle)
        raise DemoLaunchError("Could not manage the Windows service process tree")
    return int(handle)


def _close_windows_job(handle: int) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(wintypes.HANDLE(handle))


def _resume_windows_process(process: subprocess.Popen[bytes]) -> None:
    """Resume the suspended primary thread after safe Job assignment."""
    from ctypes import wintypes

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if not snapshot or int(snapshot) == invalid_handle:
        raise DemoLaunchError("Could not inspect the suspended Windows service")

    entry = THREADENTRY32()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        available = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while available:
            if entry.th32OwnerProcessID == process.pid:
                thread = kernel32.OpenThread(0x0002, False, entry.th32ThreadID)
                if thread:
                    try:
                        if kernel32.ResumeThread(thread) != 0xFFFFFFFF:
                            return
                    finally:
                        kernel32.CloseHandle(thread)
            available = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    raise DemoLaunchError("Could not resume the managed Windows service")


def _spawn(
    command: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[bytes]:
    options: dict[str, object] = {"cwd": cwd}
    if env is not None:
        options["env"] = env
    if os.name == "nt":
        options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | WINDOWS_CREATE_SUSPENDED
        )
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, **options)  # type: ignore[arg-type]
    if os.name == "nt":
        job_handle: int | None = None
        try:
            job_handle = _create_windows_job(process)
            process._demo_job_handle = job_handle  # type: ignore[attr-defined]
            _resume_windows_process(process)
        except DemoLaunchError:
            if job_handle is not None:
                _close_windows_job(job_handle)
            else:
                process.kill()
            raise
    return process


def _wait_until_ready(service: Service, process: Process) -> None:
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while True:
        return_code = process.poll()
        if return_code is not None:
            raise DemoLaunchError(
                f"{service.name} exited before becoming ready (code {return_code})"
            )
        if _is_healthy(service):
            return
        if time.monotonic() >= deadline:
            raise DemoLaunchError(
                f"{service.name} did not become ready within 30 seconds"
            )
        time.sleep(POLL_INTERVAL_SECONDS)


def _ensure_service(
    service: Service,
    *,
    command: tuple[str, ...] | Callable[[], tuple[str, ...]],
    cwd: Path,
    output: IO[str],
    env: dict[str, str] | None = None,
) -> Process | None:
    if _is_healthy(service):
        print(f"Reusing healthy {service.name} on port {service.port}", file=output)
        return None
    if _port_in_use(service):
        raise DemoLaunchError(
            f"Port {service.port} is occupied but {service.name} is not healthy"
        )
    print(f"Starting {service.name} on port {service.port}", file=output)
    resolved_command = command() if callable(command) else command
    process = _spawn(resolved_command, cwd=cwd, env=env)
    try:
        _wait_until_ready(service, process)
    except BaseException:
        _shutdown_process(process)
        raise
    return process


def _run_fault_demo() -> int:
    completed = subprocess.run(
        (sys.executable, "-m", "demo.run_demo"),
        cwd=ROOT,
        check=False,
    )
    return completed.returncode


def _wait_for_interrupt(processes: list[Process]) -> None:
    while True:
        for process in processes:
            return_code = process.poll()
            if return_code is not None:
                raise DemoLaunchError(
                    f"A launcher-managed service exited unexpectedly (code {return_code})"
                )
        time.sleep(0.5)


def _shutdown_process(process: Process) -> None:
    root_is_running = process.poll() is None
    if os.name == "nt":
        job_handle = vars(process).get("_demo_job_handle")
        if not root_is_running:
            if isinstance(job_handle, int):
                _close_windows_job(job_handle)
                setattr(process, "_demo_job_handle", None)
            return
        try:
            subprocess.run(
                ("taskkill", "/PID", str(process.pid), "/T"),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
            if isinstance(job_handle, int):
                _close_windows_job(job_handle)
                setattr(process, "_demo_job_handle", None)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        if isinstance(job_handle, int):
            _close_windows_job(job_handle)
            setattr(process, "_demo_job_handle", None)
            return
        try:
            subprocess.run(
                ("taskkill", "/PID", str(process.pid), "/T", "/F"),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        pass

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        return


def run(*, output: IO[str] = sys.stdout, error: IO[str] = sys.stderr) -> int:
    owned_processes: list[Process] = []
    try:
        settings = Settings()
        _validate_settings(settings)
        api = _ensure_service(
            API_SERVICE,
            command=_api_command(sys.executable),
            cwd=ROOT,
            output=output,
            env=_api_environment(settings),
        )
        if api is not None:
            owned_processes.append(api)

        console = _ensure_service(
            CONSOLE_SERVICE,
            command=lambda: _console_command(_find_npm()),
            cwd=CONSOLE_DIR,
            output=output,
        )
        if console is not None:
            owned_processes.append(console)

        if _run_fault_demo() != 0:
            raise DemoLaunchError("Fault simulation failed")

        print("API:     http://127.0.0.1:8020", file=output)
        print("Console: http://localhost:5173", file=output)
        print("Press Ctrl+C to stop launcher-managed services.", file=output)
        _wait_for_interrupt(owned_processes)
    except KeyboardInterrupt:
        print("Stopping demo services...", file=output)
        return 0
    except DemoLaunchError as exc:
        print(f"Demo launcher error: {exc}", file=error)
        return 1
    finally:
        for process in reversed(owned_processes):
            _shutdown_process(process)
    return 0


def main() -> None:
    raise SystemExit(run())


__all__ = ["DemoLaunchError", "main", "run"]
