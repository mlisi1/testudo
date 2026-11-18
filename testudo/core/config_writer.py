"""Saves changes made live back to the loaded config file, through `config_editor`."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable

from testudo.core.config import ExcludeMatchType, ExcludeRule
from testudo.core.config_editor import Path as ConfigPath, load_document, remove_value, set_value, sync_list


def _render_exclude_rule(rule: ExcludeRule) -> Any:
    """The YAML-serializable form of `rule`: a plain string for `literal`, a mapping for `regex`."""
    if rule.match_type is ExcludeMatchType.LITERAL:
        return rule.pattern
    return {"pattern": rule.pattern, "type": rule.match_type.value}


def _declared_entry(data: dict[str, Any], topic: str) -> ConfigPath | None:
    """Where `topic` is declared: its action's entry for an action status topic, else its `topics:` entry."""
    actions = data.get("actions")
    for index, action in enumerate(actions if isinstance(actions, list) else []):
        if isinstance(action, dict) and f"/{str(action.get('name', '')).lstrip('/')}/_action/status" == topic:
            return ("actions", index)
    topics = data.get("topics")
    for msg_type, entries in (topics if isinstance(topics, dict) else {}).items():
        for index, entry in enumerate(entries if isinstance(entries, list) else []):
            if isinstance(entry, dict) and entry.get("name") == topic:
                return ("topics", msg_type, index)
    return None


def _set_on_topic(text: str, topic: str, msg_type: str, key: ConfigPath, value: Any) -> str:
    """`text` with `value` at `key` of `topic`'s entry, declaring the topic if it isn't yet."""
    data = load_document(text)
    entry = _declared_entry(data, topic)
    if entry is None:
        topics = data.get("topics")
        existing = topics.get(msg_type) if isinstance(topics, dict) else None
        existing = existing if isinstance(existing, list) else []
        text = sync_list(text, ("topics", msg_type), [*existing, {"name": topic}])
        entry = ("topics", msg_type, len(existing))
    return set_value(text, (*entry, *key), value)


def write_code_override(path: str | Path, topic: str, msg_type: str, code: str, severity: str | None) -> None:
    """Set `codes: {code: severity}` on `topic` in the file at `path`; `severity=None` removes the override."""

    def edit(text: str) -> str:
        if severity is not None:
            return _set_on_topic(text, topic, msg_type, ("codes", code), severity)
        entry = _declared_entry(load_document(text), topic)
        return remove_value(text, (*entry, "codes", code)) if entry is not None else text

    edit_config_file(path, edit)


def write_warn_level(path: str | Path, topic: str, msg_type: str, metric: str, warn: float) -> None:
    """Set `thresholds: {metric: {warn: warn}}` on `topic` in the file at `path`, keeping its other keys."""
    edit_config_file(path, lambda text: _set_on_topic(text, topic, msg_type, ("thresholds", metric, "warn"), warn))


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


def write_setting(path: str | Path, key: ConfigPath, value: Any) -> None:
    """Set one plain setting, such as `("faults", "retention")`, in the file at `path`."""
    edit_config_file(path, lambda text: set_value(text, key, value))


def write_exclude_rules(path: str | Path, rules: list[ExcludeRule]) -> None:
    """Make the `exclude_topics:` list in the file at `path` hold exactly `rules`.

    Backs the Options screen's "exclude rules are persistent" promise. Rules
    that stay keep their lines and comments, and the rest of the file isn't touched.
    """
    items = [_render_exclude_rule(rule) for rule in rules]
    edit_config_file(path, lambda text: sync_list(text, ("exclude_topics",), items))
