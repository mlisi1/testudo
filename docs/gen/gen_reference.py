#!/usr/bin/env python3
"""Writes the reference tables the docs include, from the code they describe.

    python3 docs/gen/gen_reference.py

Default levels, CLI help and the code index live under `docs/includes/`.
`tests/test_docs.py` fails when a committed file differs from what this writes.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from testudo import cli
from testudo.core.config import NodeProfilingConfig
from testudo.core.stamp_check import (
    DEFAULT_FUTURE_LEVELS,
    DEFAULT_LAG_LEVELS,
    STAMP_FUTURE_METRIC,
    STAMP_LAG_METRIC,
)
from testudo.plugins.base import SeverityLevels
from testudo.plugins.builtin.actions import ActionsPlugin
from testudo.plugins.builtin.bt_log import BehaviorTreeLogPlugin
from testudo.plugins.builtin.costmap import CostmapPlugin
from testudo.plugins.builtin.gnss import GnssPlugin
from testudo.plugins.builtin.image_stream import ImageStreamPlugin
from testudo.plugins.builtin.imu import ImuPlugin
from testudo.plugins.builtin.localization import LocalizationPlugin
from testudo.plugins.builtin.localization_roles import CONTINUOUS_ROLE, GLOBAL_ROLE, ROLE_LEVELS
from testudo.plugins.builtin.point_stream import PointStreamPlugin
from testudo.plugins.builtin.tf_watch import TFWatchPlugin
from testudo.plugins.builtin.velocity_command import VelocityCommandPlugin

DOCS = Path(__file__).resolve().parents[1]
INCLUDES = DOCS / "includes"

#: Each documented plugin: its page under `docs/plugins/` and its class.
PLUGIN_PAGES = {
    "localization": LocalizationPlugin,
    "imu": ImuPlugin,
    "gnss": GnssPlugin,
    "image-stream": ImageStreamPlugin,
    "point-stream": PointStreamPlugin,
    "velocity-command": VelocityCommandPlugin,
    "costmap": CostmapPlugin,
    "actions": ActionsPlugin,
    "bt-log": BehaviorTreeLogPlugin,
    "tf-watch": TFWatchPlugin,
}

#: Where each code prefix is documented, relative to `docs/`.
CODE_PAGES = {
    "LOC": "plugins/localization.md",
    "IMU": "plugins/imu.md",
    "GNSS": "plugins/gnss.md",
    "IMG": "plugins/image-stream.md",
    "SENS": "plugins/point-stream.md",
    "VEL": "plugins/velocity-command.md",
    "MAP": "plugins/costmap.md",
    "ACT": "plugins/actions.md",
    "BT": "plugins/bt-log.md",
    "TF": "plugins/tf-watch.md",
    "LIVE": "core-checks/live.md",
    "STAMP": "core-checks/stamp.md",
    "CHECK": "core-checks/check.md",
    "NODE": "core-checks/node.md",
}

CODE_HEADING = re.compile(r"^### ([A-Z]+-\d{3}) · (.+?) \{ #([a-z]+-\d{3}) \}$", re.MULTILINE)


def _cut(value: float | None) -> str:
    return "–" if value is None else f"`{value:g}`"


def levels_table(levels: dict[str, SeverityLevels]) -> str:
    """A metric/warn/error table, sorted by metric."""
    rows = ["| Metric | `warn` | `error` |", "|---|---|---|"]
    rows += [f"| `{name}` | {_cut(cut.warn)} | {_cut(cut.error)} |" for name, cut in sorted(levels.items())]
    return "\n".join(rows) + "\n"


def _command_help(parser: argparse.ArgumentParser, path: list[str]) -> list[tuple[str, str]]:
    """`(command, help text)` for `parser` and every subcommand under it."""
    found = [(" ".join(path), parser.format_help())]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, subparser in action.choices.items():
                found += _command_help(subparser, [*path, name])
    return found


def cli_reference() -> str:
    """Every command's `--help`, at a fixed width."""
    previous = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = "80"
    try:
        commands = [
            (name, text.replace(cli.DEFAULT_CONFIG_PATH, "~/.config/testudo/config.yaml"))
            for name, text in _command_help(cli._build_parser(), ["testudo"])
        ]
    finally:
        if previous is None:
            del os.environ["COLUMNS"]
        else:
            os.environ["COLUMNS"] = previous
    return "".join(f"## `{name}`\n\n```text\n{text.rstrip()}\n```\n\n" for name, text in commands).rstrip() + "\n"


def code_index() -> str:
    """Every documented code with a link to its entry, in the order the pages list them."""
    rows = ["| Code | What it means |", "|---|---|"]
    for page in CODE_PAGES.values():
        path = DOCS / page
        if path.exists():
            rows += [
                f"| [`{code}`](../{page}#{anchor}) | {title} |"
                for code, title, anchor in CODE_HEADING.findall(path.read_text(encoding="utf-8"))
            ]
    return "\n".join(rows) + "\n"


def generated_files() -> dict[Path, str]:
    """Every generated file and its content."""
    files = {INCLUDES / "levels" / f"{page}.md": levels_table(plugin.default_levels()) for page, plugin in PLUGIN_PAGES.items()}
    files[INCLUDES / "levels" / "localization-continuous.md"] = levels_table(ROLE_LEVELS[CONTINUOUS_ROLE])
    files[INCLUDES / "levels" / "localization-global.md"] = levels_table(ROLE_LEVELS[GLOBAL_ROLE])
    files[INCLUDES / "levels" / "stamp.md"] = levels_table(
        {STAMP_LAG_METRIC: DEFAULT_LAG_LEVELS, STAMP_FUTURE_METRIC: DEFAULT_FUTURE_LEVELS}
    )
    profiling = NodeProfilingConfig()
    files[INCLUDES / "levels" / "node.md"] = levels_table(
        {
            "cpu_percent": profiling.cpu_percent,
            "memory_mb": profiling.memory_mb,
            "ctx_switches_per_s": profiling.ctx_switches_per_s,
            "memory_slope_mb_per_min": profiling.memory_slope_mb_per_min,
        }
    )
    files[INCLUDES / "cli.md"] = cli_reference()
    files[INCLUDES / "codes.md"] = code_index()
    return files


def main() -> None:
    """Write every generated file."""
    for path, content in generated_files().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
