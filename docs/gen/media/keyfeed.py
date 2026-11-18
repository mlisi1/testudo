"""Run the testudo CLI, pressing every key name appended to the file $KEYFEED.

Lets `capture.py` drive the real dashboard without X input injection. Touches
`$KEYFEED.ready` once the dashboard is up.
"""
import os
import sys

from testudo.cli import main
from testudo.tui import app as app_module

_FEED = os.environ["KEYFEED"]
_POLL_SECONDS = 0.1
_original_on_mount = app_module.TestudoApp.on_mount


def _on_mount(self):
    """Start polling the feed once the app is mounted."""
    result = _original_on_mount(self)
    open(_FEED, "w").close()
    position = 0

    def poll() -> None:
        nonlocal position
        with open(_FEED) as feed:
            feed.seek(position)
            keys = feed.read().split()
            position = feed.tell()
        for key in keys:
            self.simulate_key(key)

    self.set_interval(_POLL_SECONDS, poll)
    open(f"{_FEED}.ready", "w").close()
    return result


app_module.TestudoApp.on_mount = _on_mount
sys.exit(main())
