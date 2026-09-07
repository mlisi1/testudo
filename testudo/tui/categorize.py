"""Groups TopicReport rows into display categories for the TUI's summary view.

137 topics won't fit one screen, so the summary shows one line per
category rather than one per topic (drill-down gets you the rest).
"""
from __future__ import annotations

from testudo.core.topic_report import TopicReport

#: Message types whose plugin-derived category name reads better than the
#: message type's own short name would (e.g. "GoalStatusArray" -> "Nav2 Actions").
_FRIENDLY_NAMES = {
    "action_msgs/msg/GoalStatusArray": "Nav2 Actions",
    "tf2_msgs/msg/TFMessage": "TF",
}

#: Vitals-tier topics have no plugin identity to group by, so they all land here.
OTHER_CATEGORY = "Other Topics"


def category_for(report: TopicReport) -> str:
    """The display category `report` belongs to.

    Full-tier topics are grouped by their message type's short name (or a
    friendlier override for the built-in plugins); this works for
    third-party plugins too, without Testudo needing to know their names.
    Vitals-tier topics all fall into one `OTHER_CATEGORY` bucket, since
    there's no plugin identity to distinguish them by.
    """
    if report.tier != "full":
        return OTHER_CATEGORY
    if report.msg_type in _FRIENDLY_NAMES:
        return _FRIENDLY_NAMES[report.msg_type]
    return report.msg_type.rsplit("/", 1)[-1]
