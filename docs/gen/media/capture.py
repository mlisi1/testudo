"""Record the docs' images and looping videos from the real dashboard, on a virtual display.

Usage: `python3 docs/gen/media/capture.py`, from a sourced ROS 2 Jazzy shell.
Needs Xvfb, Terminator, ffmpeg and the field bag in `bags/`. Runs on its own ROS domain.
"""
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from reels import Reel, encode, join

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OUTPUT = _REPO / "docs" / "assets" / "media"
_BAG = _REPO / "bags" / "20260722_093225_campo_ippodromo"
_DISPLAY = ":178"
_ROS_DOMAIN = "178"
_WIDTH = 1100
_HEIGHT = 640
_FPS = 15
_READY_TIMEOUT_S = 900.0
_SETTLE_S = 15.0
_STACK_START_S = 5.0
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

    def close(self) -> None:
        """Quit the dashboard and its terminal."""
        self.press("q")
        time.sleep(1.0)
        try:
            os.killpg(self._process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        self._process.wait(timeout=10)


def _grab_args() -> list[str]:
    return [
        "ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-draw_mouse", "0", "-framerate", str(_FPS),
        "-video_size", f"{_WIDTH}x{_HEIGHT}", "-i", _DISPLAY,
    ]


def still(name: str, crop: str = "iw:ih:0:0") -> None:
    """Save the part of the screen given by `crop` (w:h:x:y) as `<name>.png`."""
    output = _OUTPUT / f"{name}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([*_grab_args(), "-vf", f"crop={crop}", "-frames:v", "1", str(output)], check=True)


def record(session: Session, output: Path, seconds: float, steps: list[tuple[float, list[str]]]) -> Path:
    """Record `seconds` of the screen while pressing each step's keys after its pause."""
    recorder = subprocess.Popen(
        [*_grab_args(), "-t", str(seconds), "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", str(output)]
    )
    for pause, keys in steps:
        time.sleep(pause)
        session.press(*keys)
    recorder.wait(timeout=seconds + 15.0)
    return output


def capture_live(workdir: Path) -> Reel:
    """The costmap, the panes and the quick actions, against `fault_stack.py`."""
    config = workdir / "fault_config.yaml"  # The quick action saves to it.
    shutil.copy(_HERE / "fault_config.yaml", config)
    stack = subprocess.Popen(["nice", "-n", "19", "python3", str(_HERE / "fault_stack.py")], start_new_session=True)
    time.sleep(_STACK_START_S)
    session = Session(workdir, ["watch", "-c", str(config)])
    try:
        session.wait_ready()
        time.sleep(_SETTLE_S)
        still("tui/dashboard")
        session.press("slash", *"costmap", "enter")
        time.sleep(2.5)
        still("plugins/costmap", crop="680:512:0:101")
        session.press("escape", "escape", "slash", *"gps", "enter")
        time.sleep(1.5)
        seconds = 9.0
        clip = record(
            session, workdir / "quick.mkv", seconds, [(1.5, ["a"]), (2.5, ["w"])],
        )
        encode(clip, _OUTPUT / "clips" / "quick-actions.mp4")
        return Reel(
            clip, seconds, "Tune it on the spot",
            "Downgrade a code or set a level from the dashboard. It is saved to your config.",
        )
    finally:
        session.close()
        os.killpg(stack.pid, signal.SIGTERM)
        stack.wait(timeout=10)


def capture_replay(workdir: Path) -> list[Reel]:
    """The field bag: browsing, jumping between problems, searching and scrubbing."""
    session = Session(workdir, ["replay", "-c", str(_HERE / "field_config.yaml"), str(_BAG), "--watch"])
    try:
        session.wait_ready()
        time.sleep(3.0)
        session.press(*["left_curly_bracket"] * 6)
        time.sleep(1.0)
        browse = record(
            session, workdir / "browse.mkv", 8.0,
            [(1.0, ["j"]), (1.2, ["j"]), (1.2, ["j"]), (1.2, ["j"]), (1.2, ["j"])],
        )
        jump = record(
            session, workdir / "jump.mkv", 8.0, [(1.0, ["n"]), (1.4, ["n"]), (1.4, ["n"]), (1.4, ["n"])],
        )
        search = record(
            session, workdir / "search.mkv", 7.0, [(0.5, ["escape"]), (0.8, ["slash", *"gps"]), (1.2, ["enter"]), (1.0, ["right", "j"])],
        )
        session.press("escape", "escape", "escape")
        time.sleep(1.0)
        session.press(*["right_curly_bracket"] * 6)
        time.sleep(1.0)
        scrub = record(
            session, workdir / "scrub.mkv", 9.0,
            [(1.0, ["left_curly_bracket"]), (1.2, ["left_curly_bracket"]), (1.2, ["left_square_bracket"] * 4),
             (1.0, ["right_curly_bracket"]), (1.2, ["right_square_bracket"] * 4)],
        )
    finally:
        session.close()
    encode(jump, _OUTPUT / "clips" / "jump.mp4")
    encode(scrub, _OUTPUT / "clips" / "scrub.mp4")
    return [
        Reel(browse, 8.0, "See the whole stack at once",
             "Every topic is judged and grouped by what it is. Move the cursor and the other panes follow."),
        Reel(jump, 8.0, "The worst problems come first",
             "Needs attention ranks what is wrong. Press n to jump straight to the next problem."),
        Reel(search, 7.0, "Find any topic in a keystroke",
             "Search narrows the dashboard to the topics that match."),
        Reel(scrub, 9.0, "Rewind to what broke first",
             "Scrub through a recorded run and watch each fault appear and clear."),
    ]


def main() -> None:
    """Record everything and assemble the home page's reels."""
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
        reels = capture_replay(workdir)
        reels.append(capture_live(workdir))
        join(reels, _WIDTH, _OUTPUT / "home" / "hero.mp4", workdir)
    finally:
        display.terminate()
        display.wait(timeout=10)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
