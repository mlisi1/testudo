"""The settings under Options > Checks: what each may be set to, and applying one to a running session."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from testudo.core.config import FaultRetention, FaultsConfig, NodeProfilingConfig, SeverityMode

if TYPE_CHECKING:
    from testudo.core.fault_history import FaultHistory
    from testudo.core.node_profiling.profiler import NodeProfiler
    from testudo.core.subscription_manager import SubscriptionManager


@dataclass(frozen=True)
class CheckSetting:
    """One setting: `key` is its dotted path in the config file, `help` what it does, for the Options screen."""

    key: str
    label: str
    choices: tuple[Any, ...]
    help: str


CHECK_SETTINGS = (
    CheckSetting(
        "severity_mode",
        "Severity mode",
        tuple(mode.value for mode in SeverityMode),
        "How topics roll up into the overall verdict.\n"
        "worst: the worst topic decides.\n"
        "weighted: the average, using each topic's weight.\n"
        "both: worst decides, with the weighted health beside it.",
    ),
    CheckSetting(
        "max_check_rate_hz",
        "Check rate (Hz)",
        (5.0, 10.0, 20.0, 50.0),
        "How often each topic's content is checked, at most. Higher catches shorter faults and uses more CPU. "
        "Liveness and Hz always see every message.",
    ),
    CheckSetting(
        "debounce_samples",
        "Debounce samples",
        (1, 2, 3, 5, 10),
        "Samples in a row needed to change a topic's severity. Higher stops flapping, lower reacts sooner.",
    ),
    CheckSetting(
        "faults.retention",
        "Fault retention",
        tuple(retention.value for retention in FaultRetention),
        "Which cleared faults stay listed.\n"
        "session: all of them, until Testudo stops.\n"
        "recent: each drops off after faults.recent_window_seconds (10 minutes by default).",
    ),
    CheckSetting(
        "node_profiling.enabled",
        "Node profiling",
        (True, False),
        "CPU, memory and leak checks on each ROS node's process, shown in the Nodes category.",
    ),
    CheckSetting(
        "node_profiling.gpu_enabled",
        "GPU profiling",
        (True, False),
        "Adds each process's GPU memory to node profiling. Needs an NVIDIA GPU and pynvml.",
    ),
)


class LiveChecks:
    """The current value of each setting, and what changing one does to the running session."""

    def __init__(
        self,
        manager: "SubscriptionManager",
        fault_history: "FaultHistory",
        node_profiler: "NodeProfiler | None",
        severity_mode: SeverityMode,
        set_severity_mode: Callable[[SeverityMode], None],
        faults: FaultsConfig,
        node_profiling: NodeProfilingConfig,
    ) -> None:
        self._manager = manager
        self._fault_history = fault_history
        self._node_profiler = node_profiler
        self._set_severity_mode = set_severity_mode
        self._values: dict[str, Any] = {
            "severity_mode": severity_mode.value,
            "max_check_rate_hz": manager._max_check_rate_hz,
            "debounce_samples": manager._hysteresis_required_consecutive,
            "faults.retention": faults.retention.value,
            "node_profiling.enabled": node_profiler is not None,
            "node_profiling.gpu_enabled": node_profiling.gpu_enabled,
        }

    def values(self) -> dict[str, Any]:
        """Each setting's current value, by key."""
        return dict(self._values)

    def apply(self, key: str, value: Any) -> bool:
        """Change one setting; False when it can only take effect on the next start."""
        self._values[key] = value
        if key == "severity_mode":
            self._set_severity_mode(SeverityMode(value))
        elif key == "max_check_rate_hz":
            self._manager._max_check_rate_hz = value
        elif key == "debounce_samples":
            self._set_debounce_samples(value)
        elif key == "faults.retention":
            self._fault_history.set_retention(FaultRetention(value))
        elif self._node_profiler is None:
            # Never started: sampling needs a timer on the node, set up at startup.
            return not value
        elif key == "node_profiling.enabled":
            self._node_profiler.set_paused(not value)
        elif key == "node_profiling.gpu_enabled":
            self._node_profiler.set_gpu_enabled(value)
        return True

    def _set_debounce_samples(self, samples: int) -> None:
        self._manager._hysteresis_required_consecutive = samples
        for state in list(self._manager._full_tier.values()):
            state.debouncer.set_required_consecutive(samples)
        if self._node_profiler is not None:
            self._node_profiler.set_debounce_samples(samples)
