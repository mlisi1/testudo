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
    "sensor_msgs/msg/Image": "ImageStream",
    "sensor_msgs/msg/CompressedImage": "ImageStream",
    "sensor_msgs/msg/LaserScan": "PointStream",
    "sensor_msgs/msg/PointCloud2": "PointStream",
    # No plugin covers this type (see subscription_manager.py's
    # `_PRESENCE_ONLY_MSG_TYPES_WITHOUT_PLUGIN`), but it's still a camera
    # frame wearing a codec's wire format -- group it with the rest of a
    # camera's topics rather than giving it its own "Packet" row.
    "theora_image_transport/msg/Packet": "ImageStream",
}

#: Vitals-tier topics have no plugin identity to group by, so they all land here.
OTHER_CATEGORY = "Other Topics"


def category_for(report: TopicReport) -> str:
    """The display category `report` belongs to.

    Full- and presence-tier topics are grouped by their message type's
    short name (or a friendlier override for the built-in plugins); this
    works for third-party plugins too, without Testudo needing to know
    their names. Presence-tier topics still have a plugin identity (or, for
    the no-plugin theora case, at least a specific message type) -- a
    plugin only defaulted them out of a live subscription, it didn't erase
    what they are -- so they get their own category row too, distinct from
    OTHER_CATEGORY's true grab-bag of vitals-tier topics with no plugin
    identity at all.
    """
    if report.tier not in ("full", "presence"):
        return OTHER_CATEGORY
    if report.msg_type in _FRIENDLY_NAMES:
        return _FRIENDLY_NAMES[report.msg_type]
    return report.msg_type.rsplit("/", 1)[-1]
