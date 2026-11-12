"""Shared test fixtures and OS safety guards for Amigo test suites."""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

_DANGEROUS_CMD_RE = re.compile(r"\b(shutdown|restart|rundll32)(?:\.exe)?\b", re.IGNORECASE)

_ORIG_SUBPROCESS_RUN = subprocess.run
_ORIG_SUBPROCESS_POPEN = subprocess.Popen
_ORIG_SUBPROCESS_CALL = subprocess.call
_ORIG_SUBPROCESS_CHECK_CALL = subprocess.check_call
_ORIG_SUBPROCESS_CHECK_OUTPUT = subprocess.check_output
_ORIG_OS_SYSTEM = os.system
_GUARD_INSTALLED = False


def _cmd_to_text(cmd) -> str:
    if isinstance(cmd, (list, tuple)):
        return " ".join(str(part) for part in cmd)
    return str(cmd or "")


def _assert_safe_command(cmd, source: str) -> None:
    text = _cmd_to_text(cmd)
    if _DANGEROUS_CMD_RE.search(text):
        raise AssertionError(
            f"[TEST-SAFETY-GUARD] Unmocked {source} call attempted dangerous command: {text!r}"
        )


def install_dangerous_command_guard() -> None:
    """Block unmocked subprocess/os.system calls invoking shutdown, restart, or rundll32."""
    global _GUARD_INSTALLED
    if _GUARD_INSTALLED:
        return

    def guarded_run(args, *pargs, **kwargs):
        _assert_safe_command(args, "subprocess.run")
        return _ORIG_SUBPROCESS_RUN(args, *pargs, **kwargs)

    class GuardedPopen(_ORIG_SUBPROCESS_POPEN):
        def __init__(self, args, *pargs, **kwargs):
            _assert_safe_command(args, "subprocess.Popen")
            super().__init__(args, *pargs, **kwargs)

    def guarded_call(args, *pargs, **kwargs):
        _assert_safe_command(args, "subprocess.call")
        return _ORIG_SUBPROCESS_CALL(args, *pargs, **kwargs)

    def guarded_check_call(args, *pargs, **kwargs):
        _assert_safe_command(args, "subprocess.check_call")
        return _ORIG_SUBPROCESS_CHECK_CALL(args, *pargs, **kwargs)

    def guarded_check_output(args, *pargs, **kwargs):
        _assert_safe_command(args, "subprocess.check_output")
        return _ORIG_SUBPROCESS_CHECK_OUTPUT(args, *pargs, **kwargs)

    def guarded_system(command):
        _assert_safe_command(command, "os.system")
        return _ORIG_OS_SYSTEM(command)

    subprocess.run = guarded_run
    subprocess.Popen = GuardedPopen
    subprocess.call = guarded_call
    subprocess.check_call = guarded_check_call
    subprocess.check_output = guarded_check_output
    os.system = guarded_system
    _GUARD_INSTALLED = True


install_dangerous_command_guard()


@contextmanager
def mock_os_side_effects():
    """Shared fixture mocking power, lock, volume, brightness, recycle-bin, window, and OS writes."""
    from amigo.services import os_automation, os_settings

    real_radio = os_settings._radio_op
    sim_radios: dict[str, str] = {}

    def sim_radio_op(kind: str, new_state: str | None = None):
        if new_state is None:
            return sim_radios.get(kind) or real_radio(kind, None)
        sim_radios[kind] = new_state
        return new_state

    mocks: dict[str, object] = {}
    with ExitStack() as stack:
        simple_targets = [
            ("amigo.services.os_automation.restart_pc", True),
            ("amigo.services.os_automation.sleep_pc", True),
            ("amigo.services.os_automation.lock_pc", True),
            ("amigo.services.os_automation.cancel_shutdown", True),
            ("amigo.services.os_automation.empty_recycle_bin", True),
            ("amigo.services.os_automation.set_volume", (True, "Volume set.")),
            ("amigo.services.os_automation.volume_up", True),
            ("amigo.services.os_automation.volume_down", True),
            ("amigo.services.os_automation.mute", True),
            ("amigo.services.os_automation.set_brightness", (True, "Brightness set.")),
            ("amigo.services.os_automation.control_windows_system", (True, "System state updated.")),
            ("amigo.services.os_automation.window_action", True),
            ("amigo.services.os_automation.close_app", True),
            ("amigo.services.os_automation.play_pause_media", True),
            ("amigo.services.os_automation.next_track", True),
            ("amigo.services.os_automation.prev_track", True),
        ]
        if hasattr(os_automation, "shutdown_pc"):
            simple_targets.append(("amigo.services.os_automation.shutdown_pc", True))

        ctrl_cls = getattr(os_automation, "WindowsSystemController", None)
        if ctrl_cls is not None:
            for m_name in ("set_power_state", "set_brightness", "set_volume", "set_radio_state"):
                if hasattr(ctrl_cls, m_name):
                    mocks[f"WindowsSystemController.{m_name}"] = stack.enter_context(
                        patch.object(ctrl_cls, m_name, return_value={"status": "success"})
                    )

        for target, ret in simple_targets:
            short = target.rsplit(".", 1)[-1]
            mocks[short] = stack.enter_context(patch(target, return_value=ret))

        for helper in (
            "_reg_write",
            "_spi_set",
            "_cmd_write",
            "_call_write",
            "_broadcast",
            "_refresh_shell",
            "_restart_explorer",
            "_run_after",
        ):
            if hasattr(os_settings, helper):
                mocks[helper] = stack.enter_context(patch.object(os_settings, helper, return_value=None))

        mocks["_radio_op"] = stack.enter_context(
            patch.object(os_settings, "_radio_op", side_effect=sim_radio_op)
        )
        mocks["_ps_radio_op"] = stack.enter_context(
            patch.object(os_settings, "_ps_radio_op", side_effect=sim_radio_op)
        )
        mocks["sim_radios"] = sim_radios
        yield mocks


class SafeOSTestCase(unittest.TestCase):
    """Base TestCase applying mock_os_side_effects() to every test method."""

    def setUp(self) -> None:
        super().setUp()
        self._os_mock_ctx = mock_os_side_effects()
        self.os_mocks = self._os_mock_ctx.__enter__()
        self.addCleanup(self._os_mock_ctx.__exit__, None, None, None)

