"""Each plugin page's screenshot and clip: a topic going from healthy to faulty and back."""
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from dashboard_session import HERE, OUTPUT, Session, grab_args, still
from plugin_stack import CYCLE_S, phase_s
from reels import encode

#: Into the healthy phase, so a clip of one whole cycle loops without a visible jump.
_CLIP_START_PHASE_S = 2.0
#: How far into the clip the screenshot is taken: late in the fault, once every code is up.
_STILL_AFTER_S = 16.5
_FIRST_SETTLE_S = 25.0
_STACK_START_S = 5.0


@dataclass(frozen=True)
class Scene:
    """A plugin page and the text that finds its topic in the dashboard's search."""

    page: str
    search: str


SCENES = (
    Scene("localization", "odom"),
    Scene("imu", "imu"),
    Scene("gnss", "gps"),
    Scene("image-stream", "camera"),
    Scene("point-stream", "scan"),
    Scene("velocity-command", "cmd"),
    Scene("costmap", "costmap"),
    Scene("actions", "navigate"),
    Scene("bt-log", "behavior"),
    Scene("tf-watch", "tf"),
)


def stack_process(script: str) -> subprocess.Popen:
    """Start one of the demo stacks in its own process group."""
    return subprocess.Popen(["nice", "-n", "19", "python3", str(HERE / script)], start_new_session=True)


def _wait_for_clip_start() -> None:
    time.sleep((_CLIP_START_PHASE_S - phase_s()) % CYCLE_S)


def _record_scene(scene: Scene, workdir: Path) -> None:
    """One whole cycle of the highlighted topic, with the screenshot taken during its fault."""
    raw = workdir / f"{scene.page}.mkv"
    _wait_for_clip_start()
    recorder = subprocess.Popen(
        [*grab_args(), "-t", str(CYCLE_S), "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", str(raw)]
    )
    time.sleep(_STILL_AFTER_S)
    still(f"plugins/{scene.page}")
    recorder.wait(timeout=CYCLE_S + 15.0)
    encode(raw, OUTPUT / "plugins" / f"{scene.page}.mp4")
    raw.unlink()


def capture_plugins(workdir: Path) -> None:
    """Record every scene against `plugin_stack.py`."""
    stack = stack_process("plugin_stack.py")
    time.sleep(_STACK_START_S)
    session = Session(workdir, ["watch", "-c", str(HERE / "plugin_config.yaml")])
    try:
        session.wait_ready()
        time.sleep(_FIRST_SETTLE_S)
        for scene in SCENES:
            session.press("escape", "escape", "slash", *scene.search, "enter")
            time.sleep(1.0)
            _record_scene(scene, workdir)
    finally:
        session.close()
        os.killpg(stack.pid, signal.SIGTERM)
        stack.wait(timeout=10)
