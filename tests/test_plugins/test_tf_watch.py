"""Unit tests for TFWatchPlugin -- synthetic tf2_msgs/TFMessage, no live ROS graph."""
from __future__ import annotations

from geometry_msgs.msg import TransformStamped
from tf2_msgs.msg import TFMessage

from rich.text import Text

from testudo.plugins.base import Severity
from testudo.plugins.builtin.tf_watch import TFWatchPlugin


def _plain(markup: str) -> str:
    return Text.from_markup(markup).plain


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
    assert _plain(status.values["chains"]) == "✓ 1/1 watched chains connected"
    assert _plain(status.values["connected_more"]) == "✓ map → odom → base_link"
    assert status.codes == {}


def test_chain_walked_against_a_parent_shows_the_reversed_hop() -> None:
    plugin = TFWatchPlugin()
    plugin.set_watched_pairs([("base_link", "map")])
    plugin.on_tick(0.0)
    plugin.on_message("/tf", _tf_message(_transform("map", "odom"), _transform("odom", "base_link")))
    assert _plain(plugin.get_status().values["connected_more"]) == "✓ base_link ← odom ← map"


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
    assert _plain(status.values["chains"]) == "✗ map ⇢ base_link  base_link never seen"
    assert "TF-002" in status.codes


def test_two_trees_are_listed_as_roots_and_explain_a_missing_chain() -> None:
    plugin = TFWatchPlugin()
    plugin.set_watched_pairs([("map", "gps")])
    plugin.on_tick(0.0)
    plugin.on_message("/tf", _tf_message(_transform("map", "odom"), _transform("odom", "base_link")))
    plugin.on_message("/tf", _tf_message(_transform("utm", "gps")))
    status = plugin.get_status()
    assert _plain(status.values["roots"]) == "map (3) · utm (2)"
    assert _plain(status.values["chains"]).endswith("in separate trees")
    assert _plain(status.values["frames"]).startswith("5 ")
    assert _plain(status.topic_panel_value) == "5 ✗1"


def test_multi_parent_child_is_error() -> None:
    plugin = TFWatchPlugin()
    plugin.on_tick(0.0)
    plugin.on_message("/tf", _tf_message(_transform("map", "base_link")))
    plugin.on_message("/tf", _tf_message(_transform("odom", "base_link")))  # a second, different parent
    status = plugin.get_status()
    assert status.severity == Severity.ERROR
    assert "multi-parent" in status.message
    assert _plain(status.values["multi_parent"]) == "base_link ← map, odom"
    assert "TF-001" in status.codes


def test_stale_edge_is_flagged_as_extrapolation_risk() -> None:
    plugin = TFWatchPlugin(buffer_window_seconds=10.0)
    plugin.on_tick(0.0)
    plugin.on_message("/tf", _tf_message(_transform("map", "odom")))
    plugin.on_tick(20.0)  # 20s later, no new message for this edge
    status = plugin.get_status()
    assert status.severity == Severity.WARN
    assert "extrapolation risk" in status.message
    assert _plain(status.values["stale_dynamic"]) == "odom 20s"
    assert "TF-003" in status.codes


def test_tf_static_messages_merge_into_the_same_graph() -> None:
    plugin = TFWatchPlugin(related_topics={"tf_static": "/tf_static"})
    plugin.set_watched_pairs([("map", "base_link")])
    plugin.on_tick(0.0)
    plugin.on_message("/tf_static", _tf_message(_transform("map", "odom")))
    plugin.on_message("/tf", _tf_message(_transform("odom", "base_link")))
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert _plain(status.values["connected_more"]).startswith("✓ map → odom")


def test_tf_static_edges_are_never_stale() -> None:
    plugin = TFWatchPlugin(related_topics={"tf_static": "/tf_static"}, buffer_window_seconds=10.0)
    plugin.on_tick(0.0)
    plugin.on_message("/tf_static", _tf_message(_transform("base_link", "laser")))
    plugin.on_tick(60.0)  # latched: published once, never again
    status = plugin.get_status()
    assert status.severity == Severity.OK
    assert _plain(status.values["stale_dynamic"]) == "–"


def test_default_thresholds_are_empty() -> None:
    assert TFWatchPlugin.default_thresholds() == {}
