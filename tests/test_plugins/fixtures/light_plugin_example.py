"""Fixture: an example light-path plugin file, loaded by test_registry.py.

This is what a user drops into their configured `plugins_dir` — a
`CheckPlugin` subclass decorated with `@register_plugin`, no packaging needed.
"""
from __future__ import annotations

from typing import Any

from testudo.plugins.base import CheckPlugin, CheckStatus, Severity
from testudo.plugins.registry import register_plugin


@register_plugin
class ExampleLightPlugin(CheckPlugin):
    @classmethod
    def msg_types(cls) -> tuple[str, ...]:
        return ("std_msgs/msg/Bool",)

    def on_message(self, topic: str, msg: Any) -> None:
        pass

    def get_status(self) -> CheckStatus:
        return CheckStatus(severity=Severity.OK, label="example-light", message="ok")
