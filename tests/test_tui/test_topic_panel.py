"""Topic Panel widget tests: which columns a category gets."""
from __future__ import annotations

from testudo.core.topic_report import TopicReport
from testudo.plugins.base import CheckStatus, Severity


def test_other_topics_get_a_type_column_and_plugin_categories_keep_theirs() -> None:
    from testudo.tui.widgets.topic_panel import TYPE_COLUMN_HEADER, TopicDetailTable

    other = TopicReport("/temp", "sensor_msgs/msg/Temperature", "vitals", CheckStatus(Severity.OK, "alive", ""))
    assert TopicDetailTable._extra_column_header_for([other]) == TYPE_COLUMN_HEADER
    odom = TopicReport(
        "/odom", "nav_msgs/msg/Odometry", "full", CheckStatus(Severity.OK, "ok", "", topic_panel_column="σxy")
    )
    assert TopicDetailTable._extra_column_header_for([odom]) == "σxy"
    excluded = TopicReport("/x", "std_msgs/msg/Empty", "excluded", CheckStatus(Severity.OK, "excluded", ""))
    assert TopicDetailTable._extra_column_header_for([excluded]) is None
