"""One dashboard on the virtual display, and grabbing what it shows."""
import os
import signal
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
OUTPUT = REPO / "docs" / "assets" / "media"
DISPLAY = ":178"
WIDTH = 1100
HEIGHT = 640
_FPS = 15
_READY_TIMEOUT_S = 900.0
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
        command = " ".join(["exec nice -n 19 python3", str(HERE / "keyfeed.py"), *testudo_args])
        self._process = subprocess.Popen(
            [
                "terminator", "--no-dbus", "-g", str(config), "-b",
                f"--geometry={WIDTH}x{HEIGHT}+0+0", "-x", "bash", "-c", command,
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


def grab_args() -> list[str]:
    return [
        "ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-draw_mouse", "0", "-framerate", str(_FPS),
        "-video_size", f"{WIDTH}x{HEIGHT}", "-i", DISPLAY,
    ]


def still(name: str, crop: str = "iw:ih:0:0") -> None:
    """Save the part of the screen given by `crop` (w:h:x:y) as `<name>.png`."""
    output = OUTPUT / f"{name}.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([*grab_args(), "-vf", f"crop={crop}", "-frames:v", "1", str(output)], check=True)


def record(session: Session, output: Path, seconds: float, steps: list[tuple[float, list[str]]]) -> Path:
    """Record `seconds` of the screen while pressing each step's keys after its pause."""
    recorder = subprocess.Popen(
        [*grab_args(), "-t", str(seconds), "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", str(output)]
    )
    for pause, keys in steps:
        time.sleep(pause)
        session.press(*keys)
    recorder.wait(timeout=seconds + 15.0)
    return output
