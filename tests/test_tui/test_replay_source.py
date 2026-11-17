"""Replay playback: synthetic frames, no bag; plus a pilot run of the scrub keys."""
from __future__ import annotations

import asyncio

from testudo.core.fault_history import ClearedFault
from testudo.core.replay_timeline import ReplayFrame
from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity
from testudo.tui.app import TestudoApp
from testudo.tui.replay_source import ReplayDataSource, ReplayTimeline
from testudo.tui.widgets.header import TestudoHeader, replay_segment
from testudo.tui.widgets.topic_panel import TopicDetailTable


def _frame(offset_s: float, severity: int = Severity.OK, cleared: tuple[ClearedFault, ...] = ()) -> ReplayFrame:
    codes = {"LIVE-003": "stale"} if severity > Severity.OK else {}
    report = TopicReport("/odom", "nav_msgs/msg/Odometry", "full", CheckStatus(severity, "x", "", codes=codes))
    return ReplayFrame(offset_s, [report], CheckStatus(severity, "overall", ""), cleared)


def _frames() -> list[ReplayFrame]:
    fault = ClearedFault("/odom", "LIVE-003", Severity.STALE, "stale", 1.0, 3.0)
    return [_frame(0.0), _frame(1.0, Severity.STALE), _frame(2.0, Severity.STALE), _frame(3.0, cleared=(fault,))]


def test_timeline_opens_paused_on_the_last_frame() -> None:
    timeline = ReplayTimeline(_frames())
    assert timeline.index == 3 and not timeline.playing
    assert timeline.position_s == timeline.duration_s == 3.0


def test_step_clamps_and_stops_playback() -> None:
    timeline = ReplayTimeline(_frames())
    assert timeline.step(-2) and timeline.index == 1
    assert timeline.step(-10) and timeline.index == 0
    assert not timeline.step(-1)
    timeline.playing = True
    timeline.step(1)
    assert not timeline.playing


def test_playing_from_the_end_starts_over_and_stops_on_the_last_frame() -> None:
    timeline = ReplayTimeline(_frames())
    timeline.toggle_play()
    assert timeline.playing and timeline.index == 0
    assert not timeline.advance(0.5)
    assert timeline.advance(0.6) and timeline.index == 1
    assert timeline.advance(5.0) and timeline.index == 3
    assert not timeline.playing
    assert not timeline.advance(1.0)


def test_data_source_serves_the_current_frame_on_its_time_base() -> None:
    source = ReplayDataSource(_frames())
    snapshot = source.poll()
    assert snapshot.time_s == 3.0
    assert [fault.code for fault in snapshot.cleared_faults] == ["LIVE-003"]
    source.timeline.step(-2)
    assert source.poll().reports[0].status.severity == Severity.STALE
    assert not source.is_live() and source.add_exclude_rule(None) is None


def test_replay_segment_shows_position_and_state() -> None:
    assert replay_segment(102.0, 155.0, True) == "[dim]bag[/dim] ▶ 1m42s/2m35s"
    assert "‖" in replay_segment(0.0, 155.0, False)


def test_scrub_keys_move_the_dashboard_through_the_frames() -> None:
    async def body():
        app = TestudoApp(ReplayDataSource(_frames()), ros_distro="jazzy")
        async with app.run_test() as pilot:
            topics = app.screen.query_one(TopicDetailTable)
            header = app.screen.query_one(TestudoHeader)
            assert topics.selected_report.status.severity == Severity.OK
            assert header.replay == (3.0, 3.0, False)
            await pilot.press("left_square_bracket")
            assert app.latest_snapshot.time_s == 2.0
            assert topics.selected_report.status.severity == Severity.STALE
            await pilot.press("left_curly_bracket")
            assert header.replay == (0.0, 3.0, False)
            await pilot.press("p")
            assert header.replay[2] is True
            await pilot.press("right_curly_bracket")
            assert header.replay == (3.0, 3.0, False)
            assert "scrub" in str(app.screen.query_one("#hint").content)

    asyncio.run(body())
