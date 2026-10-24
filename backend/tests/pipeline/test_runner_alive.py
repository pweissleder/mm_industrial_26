"""`_alive` must only probe other processes, never signal them (Windows: os.kill = terminate)."""

import os
import subprocess
import sys

import pytest

from app.pipeline import runner


def test_own_process_uses_boot_id() -> None:
    assert runner._alive(os.getpid(), runner.BOOT_ID)
    assert not runner._alive(os.getpid(), "another-boot")
    assert not runner._alive(None, None)


def test_probe_leaves_a_live_process_running_and_sees_it_end() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert runner._alive(child.pid, "boot")
        assert child.poll() is None  # still running: the probe did not kill it
    finally:
        child.kill()
        child.wait()
    assert not runner._alive(child.pid, "boot")


@pytest.mark.skipif(os.name != "nt", reason="Windows-only path")
def test_windows_probe_does_not_call_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object) -> None:
        raise AssertionError("os.kill must not be used as a probe on Windows")

    monkeypatch.setattr(os, "kill", forbidden)
    assert runner._alive(os.getppid(), "boot")
