"""Unit tests for TFWatchPlugin -- synthetic tf2_msgs/TFMessage, no live ROS graph."""
from __future__ import annotations

from geometry_msgs.msg import TransformStamped
from tf2_msgs.msg import TFMessage

from testudo.plugins.base import Severity
from testudo.plugins.builtin.tf_watch import TFWatchPlugin


def _transform(parent: str, child: str) -> TransformStamped:
    t = TransformStamped()
    t.header.frame_id = parent
    t.child_frame_id = child
    return t


def _tf_message(*transforms: TransformStamped) -> TFMessage:
    msg = TFMessage()
    msg.transforms = list(transforms)
    return msg


def test_no_transforms_yet_is_ok() -> None:
    plugin = TFWatchPlugin()
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert "no transforms received" in status.message


def test_connected_chain_with_no_watched_pairs_is_ok() -> None:
    plugin = TFWatchPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/tf", _tf_message(_transform("map", "odom"), _transform("odom", "base_link")))
    assert plugin.get_status().severity == Severity.OK


def test_watched_pair_connected_through_intermediate_frame_is_ok() -> None:
    plugin = TFWatchPlugin()
    plugin.set_watched_pairs([("map", "base_link")])
    plugin.on_tick(0.0)
    plugin.on_message("/tf", _tf_message(_transform("map", "odom"), _transform("odom", "base_link")))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert status.values["missing_pair_count"] == "0"


def test_watched_pair_missing_link_is_error() -> None:
    plugin = TFWatchPlugin()
    plugin.set_watched_pairs([("map", "base_link")])
    plugin.on_tick(0.0)
    # base_link is never linked back to map/odom at all.
    plugin.on_message("/tf", _tf_message(_transform("map", "odom")))
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "missing chain" in status.message
    assert "map->base_link" in status.message


def test_multi_parent_child_is_error() -> None:
    plugin = TFWatchPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/tf", _tf_message(_transform("map", "base_link")))
    plugin.on_message("/tf", _tf_message(_transform("odom", "base_link")))  # a second, different parent
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "multi-parent" in status.message
    assert status.values["multi_parent_count"] == "1"


def test_stale_edge_is_flagged_as_extrapolation_risk() -> None:
    plugin = TFWatchPlugin(buffer_window_seconds=10.0)
    plugin.on_tick(0.0)
    plugin.on_message("/tf", _tf_message(_transform("map", "odom")))
    plugin.on_tick(20.0)  # 20s later, no new message for this edge
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "extrapolation risk" in status.message
    assert status.values["stale_edge_count"] == "1"


def test_tf_static_messages_merge_into_the_same_graph() -> None:
    plugin = TFWatchPlugin(related_topics={"tf_static": "/tf_static"})
    plugin.set_watched_pairs([("map", "base_link")])
    plugin.on_tick(0.0)
    plugin.on_message("/tf_static", _tf_message(_transform("map", "odom")))
    plugin.on_message("/tf", _tf_message(_transform("odom", "base_link")))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert status.values["missing_pair_count"] == "0"


def test_default_thresholds_are_empty() -> None:
    assert TFWatchPlugin.default_thresholds() == {}
