"""Unit tests for applying Options > Checks settings to a running session: no live graph."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from testudo.core.aggregator import HysteresisDebouncer
from testudo.core.config import ConfigError, FaultRetention, FaultsConfig, NodeProfilingConfig, SeverityMode
from testudo.core.config_loader import load_config
from testudo.core.fault_history import FaultHistory
from testudo.core.live_settings import CHECK_SETTINGS, LiveChecks
from testudo.plugins.base import CheckStatus, Severity


class _Profiler:
    def __init__(self) -> None:
        self.paused = False
        self.gpu = True
        self.samples = 0

    def set_paused(self, paused: bool) -> None:
        self.paused = paused

    def set_gpu_enabled(self, enabled: bool) -> None:
        self.gpu = enabled

    def set_debounce_samples(self, samples: int) -> None:
        self.samples = samples


def _checks(profiler: _Profiler | None = None) -> tuple[LiveChecks, SimpleNamespace, FaultHistory, list[SeverityMode]]:
    debouncer = HysteresisDebouncer(3)
    manager = SimpleNamespace(
        _max_check_rate_hz=10.0, _hysteresis_required_consecutive=3, _full_tier={"/a": SimpleNamespace(debouncer=debouncer)}
    )
    history = FaultHistory()
    modes: list[SeverityMode] = []
    checks = LiveChecks(
        manager, history, profiler, SeverityMode.WORST, modes.append, FaultsConfig(), NodeProfilingConfig()
    )
    return checks, manager, history, modes


def test_every_setting_has_a_current_value() -> None:
    assert set(_checks()[0].values()) == {setting.key for setting in CHECK_SETTINGS}


def test_the_severity_mode_reaches_the_session() -> None:
    checks, _manager, _history, modes = _checks()
    assert checks.apply("severity_mode", "both")
    assert modes == [SeverityMode.BOTH]


def test_the_check_rate_applies_to_the_next_message() -> None:
    checks, manager, _history, _modes = _checks()
    assert checks.apply("max_check_rate_hz", 20.0)
    assert manager._max_check_rate_hz == 20.0


def test_debounce_reaches_topics_already_subscribed_and_later_ones() -> None:
    checks, manager, _history, _modes = _checks()
    assert checks.apply("debounce_samples", 1)
    assert manager._hysteresis_required_consecutive == 1
    debouncer = manager._full_tier["/a"].debouncer
    debouncer.update(CheckStatus(Severity.OK, "l", "m"))
    assert debouncer.update(CheckStatus(Severity.WARN, "l", "m")).severity == Severity.WARN


def test_fault_retention_reaches_the_history() -> None:
    checks, _manager, history, _modes = _checks()
    assert checks.apply("faults.retention", "recent")
    assert history._retention is FaultRetention.RECENT


def test_node_profiling_pauses_and_resumes() -> None:
    profiler = _Profiler()
    checks = _checks(profiler)[0]
    assert checks.apply("node_profiling.enabled", False)
    assert profiler.paused
    assert checks.apply("node_profiling.enabled", True)
    assert not profiler.paused


def test_gpu_and_debounce_reach_the_profiler() -> None:
    profiler = _Profiler()
    checks = _checks(profiler)[0]
    checks.apply("node_profiling.gpu_enabled", False)
    checks.apply("debounce_samples", 5)
    assert (profiler.gpu, profiler.samples) == (False, 5)


def test_a_profiler_that_never_started_waits_for_the_next_start() -> None:
    checks = _checks(None)[0]
    assert checks.values()["node_profiling.enabled"] is False
    assert not checks.apply("node_profiling.enabled", True)
    assert checks.values()["node_profiling.enabled"] is True


@pytest.mark.parametrize("text", ["max_check_rate_hz: 0\n", "max_check_rate_hz: fast\n", "debounce_samples: 0\n", "debounce_samples: 1.5\n", "debounce_samples: true\n"])
def test_a_bad_value_fails_at_load(tmp_path: Path, text: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError):
        load_config(path)


def test_the_values_load_from_the_config(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("max_check_rate_hz: 20\ndebounce_samples: 5\n")
    config = load_config(path)
    assert (config.max_check_rate_hz, config.debounce_samples) == (20.0, 5)
