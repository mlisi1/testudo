"""Typed config schema and YAML loader/validator.

Config is parsed into dataclasses once at startup and validated here, so a
malformed config fails loudly and specifically at load time rather than
three modules deep with a KeyError.
"""
from __future__ import annotations

import enum
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

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


@dataclass(frozen=True)
class ActionConfig:
    """A Nav2 action server to track goal lifecycle for."""

    name: str
    action_type: str
    weight: float = 1.0
    thresholds: dict[str, ThresholdZone] = field(default_factory=dict)


@dataclass(frozen=True)
class TFPairConfig:
    """A parent/child frame pair to watch for a valid, fresh TF chain."""

    parent: str
    child: str
    weight: float = 1.0


@dataclass(frozen=True)
class PublishConfig:
    """DiagnosticArray publish settings, decoupled from each plugin's sampling rate."""

    rate_hz: float = 5.0
    topic: str = "/diagnostics"


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


def load_config(path: str | Path) -> TestudoConfig:
    """Load and validate a Testudo YAML config file.

    Raises:
        ConfigError: the file is missing, is not valid YAML, or fails schema validation.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping, got {type(raw).__name__}")
    return _parse_config(raw, source=str(path))


_TOP_LEVEL_KEYS = {"topics", "actions", "tf", "severity_mode", "publish", "plugins_dir", "exclude_topics"}


def _parse_config(raw: dict[str, Any], source: str) -> TestudoConfig:
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"{source}: unknown top-level key(s): {', '.join(sorted(unknown))}")

    plugins_dir = raw.get("plugins_dir")
    if plugins_dir is not None and not isinstance(plugins_dir, str):
        raise ConfigError(f"{source}: 'plugins_dir' must be a string")

    return TestudoConfig(
        topics=_parse_topics(raw.get("topics", {}), source),
        actions=_parse_actions(raw.get("actions", []), source),
        tf=_parse_tf(raw.get("tf", []), source),
        severity_mode=_parse_severity_mode(raw.get("severity_mode", "worst"), source),
        publish=_parse_publish(raw.get("publish", {}), source),
        plugins_dir=plugins_dir,
        exclude_topics=_parse_exclude_topics(raw.get("exclude_topics", []), source),
    )


_EXCLUDE_RULE_KEYS = {"pattern", "type"}


def _parse_exclude_topics(raw: Any, source: str) -> list[ExcludeRule]:
    if not isinstance(raw, list):
        raise ConfigError(f"{source}: 'exclude_topics' must be a list of patterns")
    return [_parse_exclude_rule(entry, source) for entry in raw]


def _parse_exclude_rule(entry: Any, source: str) -> ExcludeRule:
    """A plain string is shorthand for a literal rule; a mapping can also opt into `type: regex`."""
    if isinstance(entry, str):
        pattern, match_type = entry, ExcludeMatchType.LITERAL
    elif isinstance(entry, dict):
        unknown = set(entry) - _EXCLUDE_RULE_KEYS
        if unknown:
            raise ConfigError(f"{source}: exclude_topics entry has unknown key(s): {', '.join(sorted(unknown))}")
        pattern = entry.get("pattern")
        match_type = _parse_exclude_match_type(entry.get("type", "literal"), source)
    else:
        raise ConfigError(f"{source}: exclude_topics entries must be a string or a {{pattern, type}} mapping")
    if not isinstance(pattern, str) or not pattern:
        raise ConfigError(f"{source}: exclude_topics entries must have a non-empty string 'pattern'")
    try:
        return ExcludeRule(pattern=pattern, match_type=match_type)
    except InvalidExcludePatternError as exc:
        raise ConfigError(f"{source}: exclude_topics: {exc}") from exc


def _parse_exclude_match_type(raw: Any, source: str) -> ExcludeMatchType:
    if not isinstance(raw, str):
        raise ConfigError(f"{source}: exclude_topics entry 'type' must be a string")
    try:
        return ExcludeMatchType(raw)
    except ValueError:
        valid = ", ".join(t.value for t in ExcludeMatchType)
        raise ConfigError(f"{source}: exclude_topics entry 'type' must be one of [{valid}], got {raw!r}") from None


def _render_exclude_rule(rule: ExcludeRule) -> Any:
    """The YAML-serializable form of `rule`: a plain string for `literal`, a mapping for `regex`."""
    if rule.match_type is ExcludeMatchType.LITERAL:
        return rule.pattern
    return {"pattern": rule.pattern, "type": rule.match_type.value}


def write_exclude_rules(path: str | Path, rules: list[ExcludeRule]) -> None:
    """Rewrite `path`'s `exclude_topics:` block to exactly `rules`, leaving every other line untouched.

    Backs the Options screen's "exclude rules are persistent" promise: a
    rule added (or removed) live is written straight back to the config
    file that was loaded at startup, so it survives a restart without the
    user hand-editing YAML. Round-tripping the *whole* document through a
    YAML dumper would silently drop every comment in it (PyYAML doesn't
    preserve them) -- so only the `exclude_topics:` block itself is
    replaced (or removed, if `rules` is empty, or appended fresh if the
    file doesn't have one yet); comments anywhere else in the file survive.

    Creates `path` (and any missing parent directories) if it doesn't
    exist yet -- the default config location is a per-user file Testudo
    never requires to exist up front (an absent one just means "defaults,
    nothing declared or excluded"), so the first exclude rule added live
    from a fresh install needs somewhere to land.
    """
    path = Path(path)
    text = path.read_text() if path.exists() else ""
    if rules:
        block = yaml.safe_dump({"exclude_topics": [_render_exclude_rule(rule) for rule in rules]}, sort_keys=False)
    else:
        block = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_replace_top_level_block(text, "exclude_topics", block))


def _replace_top_level_block(text: str, key: str, replacement_block: str) -> str:
    """Swap the `key:`-headed block in `text` for `replacement_block` (empty string removes it).

    A block runs from the `key:` line up to (not including) the next
    unindented, non-blank, non-list-item line -- i.e. the next top-level
    key -- or end of file. `key` must start the line at column 0 to
    match, so a commented-out `# key:` line (as in example_config.yaml)
    is correctly left alone. A line starting with `-` at column 0 is
    still part of *this* block, not a new key: `yaml.safe_dump` (what
    `write_exclude_rules` itself uses to build `replacement_block`)
    renders a mapping's list value with its `-` items at the *same*
    indentation as the key, not nested under it -- treating that as
    "end of block" used to strip the `key:` header while leaving its own
    list items behind, orphaned with no header above them.
    """
    lines = text.splitlines(keepends=True)
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        if start is None:
            if line.startswith(f"{key}:"):
                start = i
            continue
        if line.strip() and not line[0].isspace() and not line.startswith("-"):
            end = i
            break
    if start is None:
        if not replacement_block:
            return text
        if not text:
            return replacement_block
        prefix = text if text.endswith("\n") else text + "\n"
        if prefix.strip() and not prefix.endswith("\n\n"):
            prefix += "\n"
        return prefix + replacement_block
    return "".join(lines[:start]) + replacement_block + "".join(lines[end:])


def _parse_topics(raw: Any, source: str) -> dict[str, list[TopicConfig]]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: 'topics' must be a mapping of msg type -> topic list")
    result: dict[str, list[TopicConfig]] = {}
    for msg_type, entries in raw.items():
        if not isinstance(msg_type, str):
            raise ConfigError(f"{source}: topic msg type keys must be strings, got {msg_type!r}")
        if not isinstance(entries, list):
            raise ConfigError(f"{source}: topics.{msg_type} must be a list of topic entries")
        result[msg_type] = [_parse_topic_entry(entry, msg_type, source) for entry in entries]
    return result


_TOPIC_ENTRY_KEYS = {"name", "weight", "thresholds", "related_topics"}


def _parse_topic_entry(entry: Any, msg_type: str, source: str) -> TopicConfig:
    if not isinstance(entry, dict):
        raise ConfigError(f"{source}: topics.{msg_type} entries must be mappings")
    unknown = set(entry) - _TOPIC_ENTRY_KEYS
    if unknown:
        raise ConfigError(f"{source}: topics.{msg_type} entry has unknown key(s): {', '.join(sorted(unknown))}")
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise ConfigError(f"{source}: topics.{msg_type} entry missing required string 'name'")
    weight = _parse_weight(entry.get("weight", 1.0), f"topics.{msg_type}[{name}]", source)
    thresholds = _parse_thresholds(entry.get("thresholds", {}), f"topics.{msg_type}[{name}]", source)
    related_topics = _parse_related_topics(entry.get("related_topics", {}), f"topics.{msg_type}[{name}]", source)
    return TopicConfig(
        name=name, msg_type=msg_type, weight=weight, thresholds=thresholds, related_topics=related_topics
    )


def _parse_related_topics(raw: Any, context: str, source: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: {context}.related_topics must be a mapping of logical name -> topic name")
    result: dict[str, str] = {}
    for logical_name, topic_name in raw.items():
        if not isinstance(logical_name, str) or not logical_name:
            raise ConfigError(f"{source}: {context}.related_topics has a non-string or empty key")
        if not isinstance(topic_name, str) or not topic_name:
            raise ConfigError(f"{source}: {context}.related_topics.{logical_name} must be a non-empty string")
        result[logical_name] = topic_name
    return result


def _parse_thresholds(raw: Any, context: str, source: str) -> dict[str, ThresholdZone]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: {context}.thresholds must be a mapping")
    result: dict[str, ThresholdZone] = {}
    for metric, zones in raw.items():
        if not isinstance(zones, dict):
            raise ConfigError(f"{source}: {context}.thresholds.{metric} must be a mapping with green/orange/red")
        unknown = set(zones) - {"green", "orange", "red"}
        if unknown:
            raise ConfigError(f"{source}: {context}.thresholds.{metric} has unknown key(s): {', '.join(sorted(unknown))}")
        for key, value in zones.items():
            if value is not None and not isinstance(value, (int, float)):
                raise ConfigError(f"{source}: {context}.thresholds.{metric}.{key} must be numeric")
        result[metric] = ThresholdZone(
            green=_to_float(zones.get("green")),
            orange=_to_float(zones.get("orange")),
            red=_to_float(zones.get("red")),
        )
    return result


def _to_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _parse_weight(value: Any, context: str, source: str) -> float:
    if not isinstance(value, (int, float)):
        raise ConfigError(f"{source}: {context}.weight must be numeric")
    return float(value)


_ACTION_ENTRY_KEYS = {"name", "action_type", "weight", "thresholds"}


def _parse_actions(raw: Any, source: str) -> list[ActionConfig]:
    if not isinstance(raw, list):
        raise ConfigError(f"{source}: 'actions' must be a list")
    result = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError(f"{source}: actions entries must be mappings")
        unknown = set(entry) - _ACTION_ENTRY_KEYS
        if unknown:
            raise ConfigError(f"{source}: action entry has unknown key(s): {', '.join(sorted(unknown))}")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{source}: action entry missing required string 'name'")
        action_type = entry.get("action_type")
        if not isinstance(action_type, str) or not action_type:
            raise ConfigError(f"{source}: action '{name}' missing required string 'action_type'")
        weight = _parse_weight(entry.get("weight", 1.0), f"actions[{name}]", source)
        thresholds = _parse_thresholds(entry.get("thresholds", {}), f"actions[{name}]", source)
        result.append(ActionConfig(name=name, action_type=action_type, weight=weight, thresholds=thresholds))
    return result


def _parse_tf(raw: Any, source: str) -> list[TFPairConfig]:
    if not isinstance(raw, list):
        raise ConfigError(f"{source}: 'tf' must be a list")
    result = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError(f"{source}: tf entries must be mappings")
        parent = entry.get("parent")
        if not isinstance(parent, str) or not parent:
            raise ConfigError(f"{source}: tf entry missing required string 'parent'")
        child = entry.get("child")
        if not isinstance(child, str) or not child:
            raise ConfigError(f"{source}: tf entry '{parent}' missing required string 'child'")
        weight = _parse_weight(entry.get("weight", 1.0), f"tf[{parent}->{child}]", source)
        result.append(TFPairConfig(parent=parent, child=child, weight=weight))
    return result


def _parse_severity_mode(raw: Any, source: str) -> SeverityMode:
    if not isinstance(raw, str):
        raise ConfigError(f"{source}: 'severity_mode' must be a string")
    try:
        return SeverityMode(raw)
    except ValueError:
        valid = ", ".join(mode.value for mode in SeverityMode)
        raise ConfigError(f"{source}: 'severity_mode' must be one of [{valid}], got {raw!r}") from None


def _parse_publish(raw: Any, source: str) -> PublishConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: 'publish' must be a mapping")
    unknown = set(raw) - {"rate_hz", "topic"}
    if unknown:
        raise ConfigError(f"{source}: publish has unknown key(s): {', '.join(sorted(unknown))}")
    rate_hz = raw.get("rate_hz", 5.0)
    if not isinstance(rate_hz, (int, float)) or rate_hz <= 0:
        raise ConfigError(f"{source}: publish.rate_hz must be a positive number")
    topic = raw.get("topic", "/diagnostics")
    if not isinstance(topic, str) or not topic:
        raise ConfigError(f"{source}: publish.topic must be a non-empty string")
    return PublishConfig(rate_hz=float(rate_hz), topic=topic)
