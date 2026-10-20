"""Typed config schema: the dataclasses every other module is handed.

Config is parsed into these once at startup and validated there
(`config_loader.load_config`), so a malformed config fails loudly and
specifically at load time rather than three modules deep with a KeyError.
`config_writer` writes exclude rules added live back to the file.
"""
from __future__ import annotations

import enum
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from testudo.plugins.base import ThresholdZone


def default_config_path() -> Path:
    """`$XDG_CONFIG_HOME/testudo/config.yaml`, or `~/.config/testudo/config.yaml` if unset.

    Not a path inside the installed package or the cloned repo -- config
    is per-user, per-machine state, and (via the TUI's Options screen)
    something Testudo itself writes back to; keeping it alongside a
    package's own code/documentation meant every exclude rule added live
    silently dirtied a tracked file's git status.
    """
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / "testudo" / "config.yaml"


class ConfigError(Exception):
    """Raised when a config file is missing, malformed, or fails validation."""


class InvalidExcludePatternError(ValueError):
    """Raised by `ExcludeRule` for a regex that fails to compile, or a literal that can never match."""


class SeverityMode(enum.Enum):
    """How per-topic statuses roll up into the overall reported severity."""

    WORST = "worst"
    WEIGHTED = "weighted"
    BOTH = "both"


class ExcludeMatchType(enum.Enum):
    """How an `ExcludeRule.pattern` is matched against a topic name."""

    LITERAL = "literal"
    REGEX = "regex"


#: Characters a real ROS 2 topic name can actually contain (tokens of
#: alphanumerics/underscore separated by `/`, plus `~` for a private name
#: and `{}` for a substitution). A `literal` pattern containing anything
#: outside this set -- `$`, `^`, `*`, `.`, etc. -- can never match a real
#: topic name, which is almost always someone who meant `type: regex` and
#: forgot to say so (the plain string still parses fine as a rule, so
#: without this check it fails silently: no error, no match, no row in
#: the Excluded Topics category, nothing to explain why).
_TOPIC_NAME_SAFE_CHARS_RE = re.compile(r"^[A-Za-z0-9_/~{}]+$")


@dataclass(frozen=True)
class ExcludeRule:
    """One topic-exclusion rule: an exact topic name, or a regex searched against it.

    Shared by config-file `exclude_topics:` entries and rules added live
    from the TUI's Options screen -- both go through this same type, so
    both get identical validation (a bad regex, or an unmatchable literal,
    fails the same way whether it came from YAML at startup or a text
    field at runtime).
    """

    pattern: str
    match_type: ExcludeMatchType = ExcludeMatchType.LITERAL

    def __post_init__(self) -> None:
        if self.match_type is ExcludeMatchType.REGEX:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise InvalidExcludePatternError(f"invalid regex {self.pattern!r}: {exc}") from None
        elif not _TOPIC_NAME_SAFE_CHARS_RE.match(self.pattern):
            raise InvalidExcludePatternError(
                f"literal pattern {self.pattern!r} contains character(s) no ROS topic name can have "
                "-- it can never match anything; did you mean type: regex?"
            )

    def matches(self, topic_name: str) -> bool:
        """True if `topic_name` is excluded by this rule.

        `literal` requires an exact match; `regex` is a `re.search` (not
        `fullmatch`), so `^word` / `word$` anchor a starts-with / ends-with
        check while an unanchored pattern matches anywhere in the name.
        """
        if self.match_type is ExcludeMatchType.REGEX:
            return re.search(self.pattern, topic_name) is not None
        return topic_name == self.pattern


@dataclass(frozen=True)
class TopicConfig:
    """One declared topic: gets the full (typed, content-checked) subscription tier."""

    name: str
    msg_type: str
    weight: float = 1.0
    thresholds: dict[str, ThresholdZone] = field(default_factory=dict)
    related_topics: dict[str, str] = field(default_factory=dict)
    # Publishes only when something happens (e.g. a controller's cmd_vel):
    # silence reads as "idle", never STALE.
    event_driven: bool = False


@dataclass(frozen=True)
class ActionConfig:
    """A Nav2 action server to track goal lifecycle for."""

    name: str
    action_type: str
    weight: float = 1.0
    thresholds: dict[str, ThresholdZone] = field(default_factory=dict)
    # e.g. {"feedback": "/navigate_to_pose/_action/feedback"} -- routed to
    # the same Nav2GoalPlugin instance as the derived status topic, exactly
    # like a declared topic's related_topics (see TopicConfig above).
    related_topics: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TFPairConfig:
    """A parent/child frame pair to watch for a valid, fresh TF chain."""

    parent: str
    child: str
    weight: float = 1.0


@dataclass(frozen=True)
class PublishConfig:
    """DiagnosticArray publish settings, decoupled from each plugin's sampling rate.

    `per_plugin` (the default) publishes one DiagnosticArray topic per
    plugin, `<namespace>/<plugin name>` (e.g. `/testudo/odometry`), plus
    `<namespace>/vitals` for topics no plugin checks and
    `<namespace>/overall` for the roll-up -- so a consumer subscribes to
    just the data it cares about. `per_plugin: false` publishes everything
    as one array on `topic` instead, which is what rqt_robot_monitor and
    diagnostic_aggregator listen to.
    """

    rate_hz: float = 5.0
    topic: str = "/diagnostics"
    per_plugin: bool = True
    namespace: str = "/testudo"


@dataclass(frozen=True)
class NodeProfilingConfig:
    """Process-level CPU/memory/GPU profiling for every resolvable ROS node, plus Testudo itself.

    Published on its own topic (`publish_topic`), separate from the
    topic/TF/Nav2 diagnostics stream -- process health is a different axis
    of data with its own natural cadence (`poll_interval_seconds` in
    `watch`, `publish_rate_hz` for `check`'s own timer), decoupled from
    `publish.rate_hz` above. `cpu_percent`'s default zone is generous
    (150/300, not 80/95): `psutil`'s cpu_percent can legitimately exceed
    100% for a multi-threaded process (matches `top`/`htop`), so a
    single-core-normalized default would false-positive on any busy,
    healthy multi-threaded Nav2 node. `ctx_switches_per_sec` zones the
    *involuntary* context-switch rate (the OS scheduler preempting a
    process against its will) -- the direct "is this node CPU-starved"
    signal, distinct from and complementary to `cpu_percent`: a starved
    node's CPU% can look perfectly normal (it isn't getting time to *use*
    the CPU) while this rate climbs. Defaults (50/200 per second) are
    deliberately conservative -- a *voluntary* switch (blocking on IPC,
    the vast majority of a healthy ROS node's switches) isn't counted at
    all, so even a chatty node shouldn't approach these under normal load.
    `memory_slope_mb_per_min` zones RSS growth over the last ~2 minutes of
    samples (a least-squares slope, judged only once the window spans a
    minute and the process has been up 5 minutes, past its startup
    allocation) -- leak detection, which a single memory reading can't do.
    """

    enabled: bool = True
    poll_interval_seconds: float = 2.0
    rediscovery_interval_seconds: float = 5.0
    gpu_enabled: bool = True
    exclude_nodes: list[ExcludeRule] = field(default_factory=list)
    cpu_percent: ThresholdZone = field(default_factory=lambda: ThresholdZone(green=150.0, orange=300.0))
    memory_mb: ThresholdZone = field(default_factory=lambda: ThresholdZone(green=500.0, orange=1500.0))
    ctx_switches_per_sec: ThresholdZone = field(default_factory=lambda: ThresholdZone(green=50.0, orange=200.0))
    memory_slope_mb_per_min: ThresholdZone = field(default_factory=lambda: ThresholdZone(green=10.0, orange=50.0))
    publish_topic: str = "/diagnostics/nodes"
    publish_rate_hz: float = 1.0


@dataclass(frozen=True)
class TestudoConfig:
    """The full, validated Testudo configuration."""

    topics: dict[str, list[TopicConfig]] = field(default_factory=dict)
    actions: list[ActionConfig] = field(default_factory=list)
    tf: list[TFPairConfig] = field(default_factory=list)
    severity_mode: SeverityMode = SeverityMode.WORST
    publish: PublishConfig = field(default_factory=PublishConfig)
    plugins_dir: str | None = None
    exclude_topics: list[ExcludeRule] = field(default_factory=list)
    node_profiling: NodeProfilingConfig = field(default_factory=NodeProfilingConfig)
