"""GPU profiling: absent pynvml, fake pynvml, and per-call NVMLError degradation."""
from __future__ import annotations

import builtins
import sys
import types

import pytest

from testudo.core.node_profiling import gpu


@pytest.fixture(autouse=True)
def _reset_gpu_cache():
    gpu.reset_for_testing()
    yield
    gpu.reset_for_testing()
    sys.modules.pop("pynvml", None)


def test_gpu_unavailable_when_pynvml_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pynvml":
            raise ImportError("no module named pynvml")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert gpu.gpu_available() is False
    assert gpu.per_process_gpu_memory_mb(123) is None


class _FakeProcInfo:
    def __init__(self, pid: int, used_bytes: int) -> None:
        self.pid = pid
        self.usedGpuMemory = used_bytes


class _FakeNVMLError(Exception):
    pass


def _install_fake_pynvml(compute_procs=None, graphics_procs=None, raise_on_compute=False) -> None:
    fake = types.ModuleType("pynvml")
    fake.NVMLError = _FakeNVMLError
    fake.nvmlInit = lambda: None
    fake.nvmlDeviceGetCount = lambda: 1
    fake.nvmlDeviceGetHandleByIndex = lambda index: "handle-0"

    def compute_getter(handle):
        if raise_on_compute:
            raise _FakeNVMLError("driver hiccup")
        return compute_procs or []

    fake.nvmlDeviceGetComputeRunningProcesses = compute_getter
    fake.nvmlDeviceGetGraphicsRunningProcesses = lambda handle: graphics_procs or []
    sys.modules["pynvml"] = fake


def test_gpu_available_and_per_process_memory_with_fake_pynvml() -> None:
    _install_fake_pynvml(compute_procs=[_FakeProcInfo(pid=42, used_bytes=256 * 1024 * 1024)])
    assert gpu.gpu_available() is True
    assert gpu.per_process_gpu_memory_mb(42) == pytest.approx(256.0)
    assert gpu.per_process_gpu_memory_mb(999) is None  # not among running processes


def test_one_getter_raising_degrades_that_reading_not_the_whole_call() -> None:
    _install_fake_pynvml(
        compute_procs=None,
        graphics_procs=[_FakeProcInfo(pid=42, used_bytes=128 * 1024 * 1024)],
        raise_on_compute=True,
    )
    # compute getter raises NVMLError (caught per-call), graphics getter still succeeds.
    assert gpu.per_process_gpu_memory_mb(42) == pytest.approx(128.0)


def test_availability_is_cached() -> None:
    _install_fake_pynvml()
    assert gpu.gpu_available() is True
    # Swap out the module after the first call -- cached result should stick.
    del sys.modules["pynvml"]
    assert gpu.gpu_available() is True
