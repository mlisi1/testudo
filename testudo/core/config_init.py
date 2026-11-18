"""`testudo config init`: a starting config written from the stack running now.

Every topic a plugin covers is declared, each with its plugin's default
levels as comments, ready to uncomment and edit. Three kinds of topic are
deliberately not declared, because declaring one changes how it is watched:
a topic nobody publishes (declared, its silence would be an error), a camera
or point cloud (declared, it would be subscribed to, which can load the
driver producing it; these are written commented out), and `/tf_static`
(read together with `/tf`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from testudo.core.discovery import ACTION_STATUS_SUFFIX, list_topics
from testudo.plugins.base import CheckPlugin
from testudo.plugins.registry import DiscoveredPlugin

if TYPE_CHECKING:
    import rclpy.node

#: How long a new node waits for graph discovery to reach it.
GRAPH_WAIT_SECONDS = 2.0

_ACTION_INFIX = "/_action/"
_ACTION_FEEDBACK_SUFFIX = "/_action/feedback"
_FEEDBACK_TYPE_SUFFIX = "_FeedbackMessage"
_ACTION_STATUS_TYPE = "action_msgs/msg/GoalStatusArray"

#: ROS graph bookkeeping, and what Testudo itself publishes.
_NEVER_DECLARED = frozenset({"/parameter_events", "/rosout", "/tf_static"})
_OWN_NAMESPACE = "/testudo"

#: A demonstration plugin, not a check anyone wants on their stack by default.
_DEMO_PLUGINS = frozenset({"dummy"})

_HEADER = """\
# Testudo configuration, written by `testudo config init` from the running stack.
#
# Each topic below is checked by a plugin. The commented `thresholds` are that
# plugin's defaults: uncomment a line and change it to tune that metric.
# Past `warn` a topic is WARN, past `error` it is ERROR. Localization topics
# adapt some defaults to their frame (odom or map) until you set them here.
#
# Topics not listed here still get liveness and rate checks.
# config/example_config.yaml describes every other setting.
"""

_HEAVY_NOTE = """\
  # Cameras and point clouds are left out on purpose: subscribing to one can
  # load the driver producing it. Uncomment an entry to check its content.
"""


@dataclass(frozen=True)
class GraphSummary:
    """What's running: each published topic's message types, and each action's type."""

    topics: dict[str, tuple[str, ...]]
    actions: dict[str, str]


@dataclass(frozen=True)
class RenderedConfig:
    """The config's text, and how much went into it."""

    text: str
    topics: int
    actions: int
    heavy_topics: int


def read_graph(node: "rclpy.node.Node") -> GraphSummary:
    """The topics with at least one publisher right now, and the action servers among them."""
    published = {info.name: info.msg_types for info in list_topics(node) if node.count_publishers(info.name) > 0}
    actions = {}
    for name in published:
        if name.endswith(ACTION_STATUS_SUFFIX):
            action = name[: -len(ACTION_STATUS_SUFFIX)]
            feedback = published.get(f"{action}{_ACTION_FEEDBACK_SUFFIX}", ())
            if feedback and feedback[0].endswith(_FEEDBACK_TYPE_SUFFIX):
                actions[action.lstrip("/")] = feedback[0][: -len(_FEEDBACK_TYPE_SUFFIX)]
    topics = {name: types for name, types in published.items() if _can_be_declared(name)}
    return GraphSummary(topics, actions)


def _can_be_declared(topic: str) -> bool:
    own = topic == _OWN_NAMESPACE or topic.startswith(f"{_OWN_NAMESPACE}/")
    return not own and topic not in _NEVER_DECLARED


def _number(value: float) -> str:
    """`value` as YAML reads it back as a number (`1e-05` alone would be a string)."""
    return yaml.safe_dump(value, default_flow_style=True).split("\n")[0]


def _default_lines(plugin: type[CheckPlugin], indent: int) -> list[str]:
    """`plugin`'s metrics as commented `thresholds:` lines at `indent` columns."""
    pad = " " * indent
    lines = []
    for metric, level in sorted(plugin.default_levels().items()):
        cuts = {"warn": level.warn, "error": level.error}
        written = ", ".join(f"{key}: {_number(cut)}" for key, cut in cuts.items() if cut is not None)
        if written:
            lines.append(f"{pad}#   {metric}: {{{written}}}\n")
    return [f"{pad}# thresholds:\n", *lines] if lines else []


def render_config(graph: GraphSummary, plugins: list[DiscoveredPlugin]) -> RenderedConfig:
    """The config text for `graph`, declaring what `plugins` cover."""
    plugin_by_type: dict[str, type[CheckPlugin]] = {}
    heavy_types: set[str] = set()
    for plugin in plugins:
        if plugin.name in _DEMO_PLUGINS:
            continue
        for msg_type in plugin.plugin_class.msg_types():
            plugin_by_type.setdefault(msg_type, plugin.plugin_class)
        heavy_types |= plugin.plugin_class.presence_only_msg_types()

    declared: dict[str, list[str]] = {}
    heavy: dict[str, list[str]] = {}
    for topic, msg_types in sorted(graph.topics.items()):
        msg_type = next((candidate for candidate in msg_types if candidate in plugin_by_type), None)
        if msg_type is not None and _ACTION_INFIX not in topic:
            (heavy if msg_type in heavy_types else declared).setdefault(msg_type, []).append(topic)

    lines = [_HEADER, "\n", "topics:\n" if declared else "topics: {}\n"]
    for msg_type, topics in sorted(declared.items()):
        lines.append(f"  {msg_type}:\n")
        for topic in topics:
            lines.append(f"    - name: {topic}\n")
            lines.extend(_default_lines(plugin_by_type[msg_type], 6))
    if heavy:
        lines.append(_HEAVY_NOTE)
    for msg_type, topics in sorted(heavy.items()):
        lines.append(f"  # {msg_type}:\n")
        lines.extend(f"  #   - name: {topic}\n" for topic in topics)

    actions_plugin = plugin_by_type.get(_ACTION_STATUS_TYPE)
    actions = graph.actions if actions_plugin is not None else {}
    if actions:
        lines.append("\nactions:\n")
    for name, action_type in sorted(actions.items()):
        lines.append(f"  - name: {name}\n    action_type: {action_type}\n")
        lines.extend(_default_lines(actions_plugin, 4))

    return RenderedConfig(
        "".join(lines),
        sum(map(len, declared.values())),
        len(actions),
        sum(map(len, heavy.values())),
    )
