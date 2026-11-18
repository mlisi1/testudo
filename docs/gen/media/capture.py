"""Record the docs' images and looping videos from the real dashboard, on a virtual display.

Usage: `python3 docs/gen/media/capture.py [replay] [live] [plugins]`, from a sourced ROS 2 Jazzy
shell; everything when no part is named. The home page's reels need `replay` and `live` together.
Needs Xvfb, Terminator, ffmpeg and the field bag in `bags/`. Runs on its own ROS domain.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dashboard_session import DISPLAY, HEIGHT, HERE, OUTPUT, REPO, WIDTH, Session, record, still
from plugin_scenes import capture_plugins, stack_process
from reels import Reel, encode, join

_BAG = REPO / "bags" / "20260722_093225_campo_ippodromo"
_ROS_DOMAIN = "178"
_SETTLE_S = 15.0
_STACK_START_S = 5.0


def capture_live(workdir: Path) -> Reel:
    """The costmap, the panes and the quick actions, against `fault_stack.py`."""
    config = workdir / "fault_config.yaml"  # The quick action saves to it.
    shutil.copy(HERE / "fault_config.yaml", config)
    stack = stack_process("fault_stack.py")
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
        encode(clip, OUTPUT / "clips" / "quick-actions.mp4")
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
    session = Session(workdir, ["replay", "-c", str(HERE / "field_config.yaml"), str(_BAG), "--watch"])
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
    encode(jump, OUTPUT / "clips" / "jump.mp4")
    encode(scrub, OUTPUT / "clips" / "scrub.mp4")
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
    """Record the parts named on the command line, or all of them."""
    parts = set(sys.argv[1:]) or {"replay", "live", "plugins"}
    os.environ.update(
        DISPLAY=DISPLAY,
        ROS_DOMAIN_ID=_ROS_DOMAIN,
        ROS_AUTOMATIC_DISCOVERY_RANGE="LOCALHOST",
        PYTHONPATH=f"{REPO}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    )
    display = subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", f"{WIDTH}x{HEIGHT}x24", "-nolisten", "tcp"])
    time.sleep(1.0)
    workdir = Path(tempfile.mkdtemp(prefix="testudo_media_"))
    try:
        reels = capture_replay(workdir) if "replay" in parts else []
        if "live" in parts:
            reels.append(capture_live(workdir))
        if {"replay", "live"} <= parts:
            join(reels, WIDTH, OUTPUT / "home" / "hero.mp4", workdir)
        if "plugins" in parts:
            capture_plugins(workdir)
    finally:
        display.terminate()
        display.wait(timeout=10)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
