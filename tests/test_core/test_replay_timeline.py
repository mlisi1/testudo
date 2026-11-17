"""Replay timeline: small real bags (like test_replay.py), sampled into frames."""
from __future__ import annotations

from pathlib import Path

import pytest
import rclpy
from std_msgs.msg import String

from testudo.core.replay import replay_bag
from testudo.core.replay_timeline import MAX_REPLAY_FRAMES, MIN_FRAME_INTERVAL_S, frame_interval_for, replay_timeline
from testudo.plugins.base import Severity
from tests.test_core.test_replay import _config, _plugins, _write_bag


@pytest.fixture(scope="module")
def ros_node():
    rclpy.init()
    node = rclpy.create_node("test_replay_timeline_node")
    yield node
    node.destroy_node()
    rclpy.shutdown()


def _chatter_with_a_gap(bag_path: Path) -> None:
    """10 Hz for 4 s, silent for 15 s, then 10 Hz again for 4 s."""
    stamps = [1_000_000_000 + i * 100_000_000 for i in range(40)]
    stamps += [20_000_000_000 + i * 100_000_000 for i in range(40)]
    _write_bag(bag_path, [("/chatter", "std_msgs/msg/String", String(data="x"), stamp) for stamp in stamps])


def test_frames_follow_the_bag_and_end_on_the_batch_report(tmp_path: Path, ros_node) -> None:
    bag_path = tmp_path / "gap"
    _chatter_with_a_gap(bag_path)

    frames = replay_timeline(str(bag_path), _config(), _plugins(), ros_node)

    offsets = [frame.offset_s for frame in frames]
    assert offsets == sorted(offsets)
    assert offsets[0] == pytest.approx(1.0)
    assert offsets[-1] == pytest.approx(22.9)
    severity_at = {round(frame.offset_s): frame.reports[0].status.severity for frame in frames}
    assert severity_at[2] == Severity.OK
    assert severity_at[15] > Severity.OK  # the gap: stale
    assert frames[-1].reports[0].status.severity == Severity.OK

    reports, overall = replay_bag(str(bag_path), _config(), _plugins(), ros_node)
    assert [(r.topic, r.status.severity) for r in frames[-1].reports] == [(r.topic, r.status.severity) for r in reports]
    assert frames[-1].overall.severity == overall.severity


def test_a_fault_that_cleared_mid_bag_is_in_the_later_frames_history(tmp_path: Path, ros_node) -> None:
    bag_path = tmp_path / "gap"
    _chatter_with_a_gap(bag_path)

    frames = replay_timeline(str(bag_path), _config(), _plugins(), ros_node)

    assert frames[0].cleared_faults == ()
    cleared = frames[-1].cleared_faults
    assert cleared and all(fault.topic == "/chatter" for fault in cleared)
    # On the frames' own time base: raised in the gap, cleared once messages resumed.
    assert 4.0 < cleared[-1].raised_at < cleared[0].cleared_at <= 20.0


def test_frame_interval_spaces_out_a_long_bag() -> None:
    assert frame_interval_for(None) == MIN_FRAME_INTERVAL_S
    assert frame_interval_for(155.0) == MIN_FRAME_INTERVAL_S
    assert frame_interval_for(3600.0) == pytest.approx(3600.0 / MAX_REPLAY_FRAMES)
