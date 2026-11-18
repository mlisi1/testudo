"""Record the docs' screenshots and videos from the real dashboard, on a virtual display.

Usage: `python3 docs/gen/media/capture.py [live] [replay]`, from a sourced ROS 2
Jazzy shell. Needs Xvfb, Terminator and ffmpeg. Runs on its own ROS domain.
`replay` also needs the field bag in `bags/`.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OUTPUT = _REPO / "docs" / "assets" / "media"
_BAG = _REPO / "bags" / "20260722_093225_campo_ippodromo"
_DISPLAY = ":178"
_ROS_DOMAIN = "178"
_WIDTH = 1440
_HEIGHT = 900
_READY_TIMEOUT_S = 900.0
_SETTLE_S = 20.0
_KEY_PAUSE_S = 0.25

_TERMINATOR_CONFIG = """[global_config]
  suppress_multiple_term_dialog = True
[profiles]
  [[default]]
    use_system_font = False
    font = DejaVu Sans Mono 11
    use_theme_colors = False
    background_color = "#14161c"
    foreground_color = "#e5e7eb"
    show_titlebar = False
    scrollbar_position = hidden
    cursor_blink = False
"""

#: Plugin page -> what to type in the dashboard's search to land on its topic.
_PLUGIN_SEARCHES = {
    "localization": "odom",
    "imu": "imu",
    "gnss": "gps",
    "image-stream": "camera",
    "point-stream": "scan",
    "velocity-command": "cmd",
    "costmap": "costmap",
    "actions": "navigate",
    "bt-log": "behavior",
    "tf-watch": "tf",
}


class Session:
    """One dashboard running in Terminator on the virtual display."""

    def __init__(self, workdir: Path, testudo_args: list[str]) -> None:
        self._feed = workdir / "keys"
        self._ready = workdir / "keys.ready"
        self._ready.unlink(missing_ok=True)
        config = workdir / "terminator.conf"
        config.write_text(_TERMINATOR_CONFIG)
        command = " ".join(["exec nice -n 19 python3", str(_HERE / "keyfeed.py"), *testudo_args])
        self._process = subprocess.Popen(
            [
                "terminator", "--no-dbus", "-g", str(config), "-b",
                f"--geometry={_WIDTH}x{_HEIGHT}+0+0", "-x", "bash", "-c", command,
            ],
            env={**os.environ, "KEYFEED": str(self._feed)},
            start_new_session=True,
        )

    def wait_ready(self) -> None:
        """Block until the dashboard has mounted."""
        deadline = time.monotonic() + _READY_TIMEOUT_S
        while not self._ready.exists():
            if time.monotonic() > deadline or self._process.poll() is not None:
                raise RuntimeError("the dashboard did not start")
            time.sleep(0.5)

    def press(self, *keys: str) -> None:
        """Press `keys` in order, by Textual key name."""
        for key in keys:
            with self._feed.open("a") as feed:
                feed.write(f"{key}\n")
            time.sleep(_KEY_PAUSE_S)

    def search(self, word: str) -> None:
        """Narrow the dashboard to the topics matching `word`."""
        self.press("slash", *word, "enter")
        time.sleep(1.5)

    def close(self) -> None:
        """Quit the dashboard and its terminal."""
        self.press("q")
        time.sleep(1.0)
        try:
            os.killpg(self._process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        self._process.wait(timeout=10)


def _grab(output: Path, *encoding: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-draw_mouse", "0", "-framerate", "10",
            "-video_size", f"{_WIDTH}x{_HEIGHT}", "-i", _DISPLAY, *encoding, str(output),
        ],
        check=True,
    )


def still(name: str) -> None:
    """Save the screen as `<name>.png`."""
    _grab(_OUTPUT / f"{name}.png", "-frames:v", "1")


def clip(name: str, seconds: float) -> None:
    """Record the screen for `seconds` as `<name>.mp4`."""
    _grab(
        _OUTPUT / f"{name}.mp4",
        "-t", str(seconds), "-c:v", "libx264", "-preset", "veryslow", "-crf", "30",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    )


def capture_live(workdir: Path) -> None:
    """Every plugin's faults and the dashboard's screens, against `fault_stack.py`."""
    stack = subprocess.Popen(["nice", "-n", "19", "python3", str(_HERE / "fault_stack.py")], start_new_session=True)
    session = Session(workdir, ["watch", "-c", str(_HERE / "fault_config.yaml")])
    try:
        session.wait_ready()
        time.sleep(_SETTLE_S)
        still("tui/dashboard")
        for page, word in _PLUGIN_SEARCHES.items():
            session.search(word)
            still(f"plugins/{page}")
            session.press("escape", "escape")
        session.press("question_mark")
        time.sleep(1.0)
        still("tui/help")
        session.press("escape")
        session.search("imu")
        session.press("a")
        time.sleep(1.0)
        still("tui/quick-actions")
        session.press("escape", "escape", "escape")
        session.press("o")
        time.sleep(1.0)
        still("tui/options")
        session.press("escape")
    finally:
        session.close()
        os.killpg(stack.pid, signal.SIGTERM)
        stack.wait(timeout=10)


def capture_replay(workdir: Path) -> None:
    """The field bag, scrubbed and played."""
    config = _HERE / "field_config.yaml"
    session = Session(workdir, ["replay", "-c", str(config), str(_BAG), "--watch"])
    try:
        session.wait_ready()
        time.sleep(3.0)
        still("tui/replay")
        session.press(*["left_curly_bracket"] * 6, "p")
        time.sleep(1.0)
        clip("home/hero", 16.0)
    finally:
        session.close()


def main() -> None:
    """Capture the groups named on the command line, or all of them."""
    groups = sys.argv[1:] or ["live", "replay"]
    os.environ.update(
        DISPLAY=_DISPLAY,
        ROS_DOMAIN_ID=_ROS_DOMAIN,
        ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST",
        PYTHONPATH=f"{_REPO}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    )
    display = subprocess.Popen(["Xvfb", _DISPLAY, "-screen", "0", f"{_WIDTH}x{_HEIGHT}x24", "-nolisten", "tcp"])
    time.sleep(1.0)
    workdir = Path(tempfile.mkdtemp(prefix="testudo_media_"))
    try:
        if "live" in groups:
            capture_live(workdir)
        if "replay" in groups:
            capture_replay(workdir)
    finally:
        display.terminate()
        display.wait(timeout=10)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
