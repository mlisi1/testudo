"""Unit tests for the core header stamp check -- synthetic messages, no live ROS graph."""
from __future__ import annotations

from builtin_interfaces.msg import Time
from nav_msgs.msg import Odometry
from std_msgs.msg import Header, String

from testudo.core.config import LevelsOverride, TopicConfig
from testudo.core.stamp_check import StampCheck, header_stamp_seconds, stamp_check_for
from testudo.plugins.base import CheckStatus, Severity, SeverityLevels

NOW = 1000.0


def _msg(stamp_s: float) -> Odometry:
    msg = Odometry()
    msg.header = Header(stamp=Time(sec=int(stamp_s), nanosec=int(round((stamp_s % 1) * 1e9))))
    return msg


def _judged(stamp_s: float, check: StampCheck | None = None, now: float = NOW, **kwargs) -> CheckStatus:
    check = check if check is not None else StampCheck()
    check.observe(_msg(stamp_s), now, **kwargs)
    return check.applied_to(CheckStatus(Severity.OK, "plugin", "nominal"))


def test_header_stamp_is_read_only_from_messages_that_have_one() -> None:
    assert header_stamp_seconds(_msg(12.5)) == 12.5
    assert header_stamp_seconds(String()) is None


def test_a_message_without_a_header_leaves_the_status_alone() -> None:
    check = StampCheck()
    check.observe(String(), NOW)
    status = CheckStatus(Severity.OK, "plugin", "nominal")
    assert check.applied_to(status) is status


def test_a_fresh_stamp_is_ok_and_reports_its_lag() -> None:
    status = _judged(NOW - 0.02)
    assert status.severity == Severity.OK
    assert status.codes == {}
    assert status.values["stamp_lag_s"] == "0.020"


def test_a_zero_stamp_is_an_error() -> None:
    status = _judged(0.0)
    assert status.severity == Severity.ERROR
    assert set(status.codes) == {"STAMP-001"}
    assert status.message == "header stamp is zero"


def test_a_stamp_ahead_of_the_clock_warns_then_errors() -> None:
    assert _judged(NOW + 0.05).codes == {}
    assert _judged(NOW + 0.5).code_severities == {"STAMP-002": Severity.WARN}
    assert _judged(NOW + 2.0).code_severities == {"STAMP-002": Severity.ERROR}


def test_a_stamp_behind_the_clock_warns_then_errors() -> None:
    assert _judged(NOW - 0.4).codes == {}
    assert _judged(NOW - 1.0).code_severities == {"STAMP-003": Severity.WARN}
    assert _judged(NOW - 6.0).code_severities == {"STAMP-003": Severity.ERROR}


def test_a_latched_topic_is_only_checked_for_future_stamps() -> None:
    assert _judged(NOW - 500.0, latched=True).codes == {}
    assert _judged(0.0, latched=True).codes == {}
    assert set(_judged(NOW + 2.0, latched=True).codes) == {"STAMP-002"}


def test_a_sim_clock_that_has_not_started_judges_nothing() -> None:
    assert _judged(5.0, now=0.0).codes == {}
    assert _judged(0.0, now=0.0).codes == {}


def test_another_time_base_is_a_finding_live_with_a_hint() -> None:
    status = _judged(100.0, now=1.7e9)
    assert status.code_severities == {"STAMP-003": Severity.ERROR}
    assert "use_sim_time" in status.codes["STAMP-003"]


def test_another_time_base_is_not_judged_in_replay() -> None:
    status = _judged(100.0, StampCheck(judge_other_time_base=False), now=1.7e9)
    assert status.codes == {}
    assert "stamp_lag_s" not in status.values


def test_replay_still_judges_a_stamp_on_the_same_time_base() -> None:
    assert set(_judged(NOW - 6.0, StampCheck(judge_other_time_base=False)).codes) == {"STAMP-003"}


def test_a_problem_clearing_clears_its_code() -> None:
    check = StampCheck()
    assert _judged(NOW - 6.0, check).codes
    assert _judged(NOW, check).codes == {}


def test_stamp_code_joins_the_plugin_codes_at_the_worse_severity() -> None:
    check = StampCheck()
    check.observe(_msg(NOW - 1.0), NOW)
    plugin_status = CheckStatus(
        Severity.ERROR, "plugin", "bad", codes={"LOC-001": "bad"}, code_severities={"LOC-001": Severity.ERROR}
    )
    status = check.applied_to(plugin_status)
    assert status.severity == Severity.ERROR
    assert status.message == "bad"
    assert status.code_severities == {"LOC-001": Severity.ERROR, "STAMP-003": Severity.WARN}


def test_lag_is_published_only_next_to_a_plugins_own_metrics() -> None:
    check = StampCheck()
    check.observe(_msg(NOW - 0.25), NOW)
    assert check.applied_to(CheckStatus(Severity.OK, "plugin", "ok")).metrics == {}
    with_metrics = check.applied_to(CheckStatus(Severity.OK, "plugin", "ok", metrics={"x": 1.0}))
    assert with_metrics.metrics == {"x": 1.0, "stamp_lag_s": 0.25}


def test_config_overrides_the_levels_key_by_key() -> None:
    config = TopicConfig(
        name="/slam/pose",
        msg_type="nav_msgs/msg/Odometry",
        levels={
            "stamp_lag_s": LevelsOverride(warn=2.0, given=frozenset({"warn"})),
            "stamp_future_s": LevelsOverride(warn=None, error=None, given=frozenset({"warn", "error"})),
        },
    )
    check = stamp_check_for(config)
    assert check.lag_levels == SeverityLevels(warn=2.0, error=5.0)
    assert _judged(NOW + 100.0, check).codes == {}


def test_an_undeclared_topic_gets_the_defaults() -> None:
    assert stamp_check_for(None) == StampCheck()
