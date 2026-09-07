"""A trivial plugin proving the CheckPlugin interface end-to-end.

Not a real check — it exists to validate that registration (via the
`testudo.checks` entry-point group), message delivery, ticking, and status
reporting all work before any real content-check plugin is written.
"""
from __future__ import annotations

from typing import Any

from testudo.plugins.base import CheckPlugin, CheckStatus, Severity, ThresholdZone


class DummyPlugin(CheckPlugin):
    """Counts messages received on its topic and reports OK with that count."""

    def __init__(
        self,
        thresholds: dict[str, ThresholdZone] | None = None,
        related_topics: dict[str, str] | None = None,
    ) -> None:
        super().__init__(thresholds, related_topics)
        self._message_count = 0

    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("std_msgs/msg/Empty",)

    @classmethod
    def default_thresholds(cls) -> dict[str, ThresholdZone]:
        return {"message_count": ThresholdZone(green=1.0)}

    def on_message(self, topic: str, msg: Any) -> None:
        self._message_count += 1

    def get_status(self) -> CheckStatus:
        return CheckStatus(
            severity=Severity.OK,
            label="dummy",
            message=f"received {self._message_count} message(s)",
            values={"message_count": str(self._message_count)},
        )
