"""Contracts for the cross-platform showcase launcher."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
import os
import runpy
import subprocess
import unittest
from unittest.mock import Mock, call, patch


class _SecretThatMustNotBeRead:
    def get_secret_value(self) -> str:
        raise AssertionError("the launcher must not read secret contents")


class _Settings:
    networkops_workflow_factory = "trusted.factory:create_workflow"
    jwt_secret_key = _SecretThatMustNotBeRead()


class _Process:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self) -> None:
        return None


class DemoLauncherTests(unittest.TestCase):
    def test_commands_are_fixed_and_windows_uses_npm_cmd(self) -> None:
        from demo import launcher

        self.assertEqual(
            launcher._api_command("python"),
            (
                "python",
                "-m",
                "uvicorn",
                "deployment.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8020",
            ),
        )
        self.assertEqual(
            launcher._console_command("npm.cmd"),
            (
                "npm.cmd",
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                "5173",
            ),
        )
        with patch("demo.launcher.shutil.which", return_value="npm.cmd") as which:
            self.assertEqual(launcher._find_npm("nt"), "npm.cmd")
        which.assert_called_once_with("npm.cmd")

    def test_configuration_check_never_reads_secret_contents(self) -> None:
        from demo import launcher

        launcher._validate_settings(_Settings())
        missing_factory = type(
            "MissingFactory",
            (),
            {
                "networkops_workflow_factory": None,
                "jwt_secret_key": _SecretThatMustNotBeRead(),
            },
        )()
        launcher._validate_settings(missing_factory)
        before = dict(os.environ)
        child_environment = launcher._api_environment(missing_factory)
        self.assertEqual(
            child_environment["NETWORKOPS_WORKFLOW_FACTORY"],
            "demo.networkops_local_factory:create_workflow",
        )
        self.assertEqual(dict(os.environ), before)

        legacy_factory = type(
            "LegacyFactory",
            (),
            {
                "networkops_workflow_factory": (
                    "networkops_local_factory:create_workflow"
                ),
                "jwt_secret_key": _SecretThatMustNotBeRead(),
            },
        )()
        self.assertEqual(
            launcher._api_environment(legacy_factory)[
                "NETWORKOPS_WORKFLOW_FACTORY"
            ],
            "demo.networkops_local_factory:create_workflow",
        )
        self.assertEqual(
            launcher._api_environment(_Settings())[
                "NETWORKOPS_WORKFLOW_FACTORY"
            ],
            "trusted.factory:create_workflow",
        )
        with self.assertRaisesRegex(launcher.DemoLaunchError, "JWT_SECRET_KEY"):
            launcher._validate_settings(
                type(
                    "MissingSecret",
                    (),
                    {
                        "networkops_workflow_factory": "factory:create",
                        "jwt_secret_key": None,
                    },
                )()
            )

    def test_healthy_services_are_reused_without_start_or_cleanup(self) -> None:
        from demo import launcher

        output = StringIO()
        before = dict(os.environ)
        with (
            patch("demo.launcher.Settings", return_value=_Settings()),
            patch("demo.launcher._is_healthy", side_effect=[True, True]),
            patch("demo.launcher._spawn") as spawn,
            patch("demo.launcher._run_fault_demo", return_value=0) as fault_demo,
            patch("demo.launcher._wait_for_interrupt", side_effect=KeyboardInterrupt),
            patch("demo.launcher._shutdown_process") as shutdown,
        ):
            result = launcher.run(output=output, error=StringIO())

        self.assertEqual(result, 0)
        self.assertEqual(before, dict(os.environ))
        spawn.assert_not_called()
        shutdown.assert_not_called()
        fault_demo.assert_called_once()
        self.assertIn("API:     http://127.0.0.1:8020", output.getvalue())
        self.assertIn("Console: http://localhost:5173", output.getvalue())

    def test_healthy_console_does_not_require_local_npm(self) -> None:
        from demo import launcher

        with (
            patch("demo.launcher.Settings", return_value=_Settings()),
            patch("demo.launcher._is_healthy", side_effect=[True, True]),
            patch(
                "demo.launcher._find_npm",
                side_effect=AssertionError("npm must be resolved lazily"),
            ),
            patch("demo.launcher._run_fault_demo", return_value=0),
            patch("demo.launcher._wait_for_interrupt", side_effect=KeyboardInterrupt),
        ):
            self.assertEqual(
                launcher.run(output=StringIO(), error=StringIO()),
                0,
            )

    def test_unhealthy_occupied_port_is_rejected_without_killing_it(self) -> None:
        from demo import launcher

        with (
            patch("demo.launcher._is_healthy", return_value=False),
            patch("demo.launcher._port_in_use", return_value=True),
            patch("demo.launcher._spawn") as spawn,
        ):
            with self.assertRaisesRegex(launcher.DemoLaunchError, "8020"):
                launcher._ensure_service(
                    launcher.API_SERVICE,
                    command=("python",),
                    cwd=Path.cwd(),
                    output=StringIO(),
                )
        spawn.assert_not_called()

    def test_start_timeout_stops_waiting_after_thirty_seconds(self) -> None:
        from demo import launcher

        process = _Process(101)
        with (
            patch("demo.launcher._is_healthy", return_value=False),
            patch("demo.launcher.time.monotonic", side_effect=[0.0, 30.1]),
            patch("demo.launcher.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(launcher.DemoLaunchError, "30 seconds"):
                launcher._wait_until_ready(launcher.API_SERVICE, process)
        sleep.assert_not_called()

    def test_readiness_polling_uses_quarter_second_interval(self) -> None:
        from demo import launcher

        process = _Process(102)
        with (
            patch("demo.launcher._is_healthy", side_effect=[False, True]),
            patch("demo.launcher.time.monotonic", side_effect=[0.0, 0.1]),
            patch("demo.launcher.time.sleep") as sleep,
        ):
            launcher._wait_until_ready(launcher.API_SERVICE, process)
        sleep.assert_called_once_with(0.25)

    def test_child_exit_is_reported_before_health_timeout(self) -> None:
        from demo import launcher

        process = Mock(pid=103)
        process.poll.return_value = 4
        with patch("demo.launcher._is_healthy") as healthy:
            with self.assertRaisesRegex(launcher.DemoLaunchError, "code 4"):
                launcher._wait_until_ready(launcher.API_SERVICE, process)
        healthy.assert_not_called()

    def test_windows_shutdown_forces_process_tree_after_five_seconds(self) -> None:
        from demo import launcher

        process = Mock(pid=104)
        process.poll.side_effect = [None, None]
        process.wait.side_effect = subprocess.TimeoutExpired("service", 5)
        with (
            patch("demo.launcher.os.name", "nt"),
            patch("demo.launcher.subprocess.run") as run,
        ):
            launcher._shutdown_process(process)

        process.wait.assert_called_once_with(timeout=5.0)
        self.assertEqual(
            run.call_args_list,
            [
                call(
                    ("taskkill", "/PID", "104", "/T"),
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ),
                call(
                    ("taskkill", "/PID", "104", "/T", "/F"),
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ),
            ],
        )

    def test_windows_graceful_shutdown_targets_the_owned_process_tree(self) -> None:
        from demo import launcher

        process = Mock(pid=105)
        process._demo_job_handle = 501
        process.poll.return_value = None
        process.wait.return_value = 0
        with (
            patch("demo.launcher.os.name", "nt"),
            patch("demo.launcher.subprocess.run") as run,
            patch("demo.launcher._close_windows_job") as close_job,
        ):
            launcher._shutdown_process(process)

        process.wait.assert_called_once_with(timeout=5.0)
        run.assert_called_once_with(
            ("taskkill", "/PID", "105", "/T"),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        close_job.assert_called_once_with(501)

    def test_cleanup_still_targets_tree_after_root_process_exits(self) -> None:
        from demo import launcher

        process = Mock(pid=106)
        process._demo_job_handle = 502
        process.poll.return_value = 9
        with (
            patch("demo.launcher.os.name", "nt"),
            patch("demo.launcher.subprocess.run") as run,
            patch("demo.launcher._close_windows_job") as close_job,
        ):
            launcher._shutdown_process(process)

        run.assert_not_called()
        close_job.assert_called_once_with(502)

    def test_windows_spawn_assigns_an_owned_job_object(self) -> None:
        from demo import launcher

        process = Mock(pid=107)
        with (
            patch("demo.launcher.os.name", "nt"),
            patch("demo.launcher.subprocess.Popen", return_value=process) as popen,
            patch("demo.launcher._create_windows_job", return_value=503) as create_job,
            patch("demo.launcher._resume_windows_process") as resume,
        ):
            result = launcher._spawn(("service",), cwd=Path("console"))

        self.assertIs(result, process)
        self.assertEqual(process._demo_job_handle, 503)
        popen.assert_called_once_with(
            ("service",),
            cwd=Path("console"),
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP
                | launcher.WINDOWS_CREATE_SUSPENDED
            ),
        )
        create_job.assert_called_once_with(process)
        resume.assert_called_once_with(process)

    def test_demo_failure_cleans_up_only_started_services(self) -> None:
        from demo import launcher

        api = _Process(201)
        console = _Process(202)
        output = StringIO()
        error = StringIO()
        with (
            patch("demo.launcher.Settings", return_value=_Settings()),
            patch(
                "demo.launcher._ensure_service",
                side_effect=[api, console],
            ),
            patch("demo.launcher._run_fault_demo", return_value=7),
            patch("demo.launcher._shutdown_process") as shutdown,
        ):
            result = launcher.run(output=output, error=error)

        self.assertEqual(result, 1)
        self.assertEqual(shutdown.call_args_list, [call(console), call(api)])
        self.assertIn("fault simulation failed", error.getvalue().lower())

    def test_ctrl_c_cleans_up_started_services_without_printing_secrets(self) -> None:
        from demo import launcher

        api = _Process(301)
        console = _Process(302)
        output = StringIO()
        error = StringIO()
        with (
            patch("demo.launcher.Settings", return_value=_Settings()),
            patch(
                "demo.launcher._ensure_service",
                side_effect=[api, console],
            ),
            patch("demo.launcher._run_fault_demo", return_value=0),
            patch("demo.launcher._wait_for_interrupt", side_effect=KeyboardInterrupt),
            patch("demo.launcher._shutdown_process") as shutdown,
        ):
            result = launcher.run(output=output, error=error)

        rendered = output.getvalue() + error.getvalue()
        self.assertEqual(result, 0)
        self.assertEqual(shutdown.call_args_list, [call(console), call(api)])
        self.assertNotIn("trusted.factory:create_workflow", rendered)
        self.assertNotIn("Authorization", rendered)
        self.assertNotIn("JWT_SECRET_KEY=", rendered)

    def test_python_m_demo_delegates_to_launcher(self) -> None:
        with patch("demo.launcher.main") as main:
            runpy.run_module("demo", run_name="__main__")
        main.assert_called_once_with()

    def test_wrappers_and_makefile_delegate_without_environment_changes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        shell = (root / "scripts" / "demo.sh").read_text(encoding="utf-8")
        powershell = (root / "scripts" / "demo.ps1").read_text(encoding="utf-8")
        makefile = (root / "Makefile").read_text(encoding="utf-8")

        self.assertIn("-m demo", shell)
        self.assertIn("-m demo", powershell)
        self.assertNotIn("export ", shell)
        self.assertNotIn("$env:", powershell)
        self.assertIn("PYTHON ?= python3", makefile)
        self.assertIn("NPM ?= npm", makefile)
        self.assertIn("$(PYTHON) -m demo", makefile)


if __name__ == "__main__":
    unittest.main()
