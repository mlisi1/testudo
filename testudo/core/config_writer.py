"""Saves changes made live back to the loaded config file, through `config_editor`."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from testudo.core.config import ExcludeMatchType, ExcludeRule
from testudo.core.config_editor import sync_list


def _render_exclude_rule(rule: ExcludeRule) -> Any:
    """The YAML-serializable form of `rule`: a plain string for `literal`, a mapping for `regex`."""
    if rule.match_type is ExcludeMatchType.LITERAL:
        return rule.pattern
    return {"pattern": rule.pattern, "type": rule.match_type.value}


def edit_config_file(path: str | Path, edit: Callable[[str], str]) -> None:
    """Apply `edit` (text in, text out) to the file at `path`, all at once or not at all.

    Creates the file and its parent directories if missing: the default
    config location need not exist before the first change is saved to it.
    Follows a symlink, keeps the file's permissions, and goes through a
    temporary file so that a crash can't leave a half-written config.
    """
    target = Path(path).resolve()
    text = target.read_text() if target.exists() else ""
    edited = edit(text)
    if edited == text and target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(descriptor, "w") as file:
            file.write(edited)
        if target.exists():
            shutil.copymode(target, temporary)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_exclude_rules(path: str | Path, rules: list[ExcludeRule]) -> None:
    """Make the `exclude_topics:` list in the file at `path` hold exactly `rules`.

    Backs the Options screen's "exclude rules are persistent" promise. Rules
    that stay keep their lines and comments, and the rest of the file isn't touched.
    """
    items = [_render_exclude_rule(rule) for rule in rules]
    edit_config_file(path, lambda text: sync_list(text, ("exclude_topics",), items))
