"""Plugin discovery: the packaged (entry points) and light (drop-in file) paths.

Kept separate from `base.py` so the plugin contract stays free of discovery
bookkeeping, and separate from the subscription/aggregation core so a change
to how plugins are found never touches how they're run.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path

from testudo.plugins.base import CheckPlugin

_logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "testudo.checks"

# Populated by @register_plugin as light-path plugin files are executed.
_light_path_registry: list[type[CheckPlugin]] = []


def register_plugin(cls: type[CheckPlugin]) -> type[CheckPlugin]:
    """Class decorator for the light path: drop a .py file in a plugins directory.

    No packaging or entry-point declaration needed; the file just needs a
    `CheckPlugin` subclass decorated with `@register_plugin`.
    """
    if not (isinstance(cls, type) and issubclass(cls, CheckPlugin)):
        raise TypeError(f"@register_plugin requires a CheckPlugin subclass, got {cls!r}")
    _light_path_registry.append(cls)
    return cls


@dataclass(frozen=True)
class DiscoveredPlugin:
    """A plugin class found by discovery, tagged with where it came from."""

    name: str
    plugin_class: type[CheckPlugin]
    source: str  # "packaged" or "light"


def discover_packaged_plugins() -> list[DiscoveredPlugin]:
    """Discover plugins exposed via `setuptools` entry points under `testudo.checks`.

    A broken entry point (import error, or resolves to something that isn't
    a CheckPlugin) is logged and skipped rather than aborting discovery.
    """
    discovered: list[DiscoveredPlugin] = []
    try:
        entry_points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        _logger.exception("failed to query entry points for group '%s'", ENTRY_POINT_GROUP)
        return discovered

    for entry_point in entry_points:
        try:
            plugin_class = entry_point.load()
        except Exception:
            _logger.exception("failed to load plugin entry point '%s'", entry_point.name)
            continue
        if not (isinstance(plugin_class, type) and issubclass(plugin_class, CheckPlugin)):
            _logger.error(
                "entry point '%s' resolves to %r, which is not a CheckPlugin subclass",
                entry_point.name,
                plugin_class,
            )
            continue
        discovered.append(DiscoveredPlugin(name=entry_point.name, plugin_class=plugin_class, source="packaged"))
    return discovered


def discover_light_path_plugins(plugins_dir: str | Path) -> list[DiscoveredPlugin]:
    """Discover plugins by executing every `.py` file in `plugins_dir`.

    Each file is expected to decorate its plugin class(es) with
    `@register_plugin`. A file that fails to import is logged and skipped.
    Returns an empty list if `plugins_dir` doesn't exist.
    """
    plugins_dir = Path(plugins_dir)
    discovered: list[DiscoveredPlugin] = []
    if not plugins_dir.is_dir():
        return discovered

    seen_before = list(_light_path_registry)
    for py_file in sorted(plugins_dir.glob("*.py")):
        module_name = f"testudo._light_plugins.{py_file.stem}"
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            _logger.error("could not build an import spec for plugin file '%s'", py_file)
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception:
            _logger.exception("error executing plugin file '%s'", py_file)
            continue

    newly_registered = [cls for cls in _light_path_registry if cls not in seen_before]
    for plugin_class in newly_registered:
        discovered.append(DiscoveredPlugin(name=plugin_class.__name__, plugin_class=plugin_class, source="light"))
    return discovered


def discover_all_plugins(plugins_dir: str | Path | None = None) -> list[DiscoveredPlugin]:
    """Discover plugins from both paths: packaged entry points, then the light path if given."""
    discovered = discover_packaged_plugins()
    if plugins_dir is not None:
        discovered.extend(discover_light_path_plugins(plugins_dir))
    return discovered
