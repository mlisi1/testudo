"""Turn raw screen recordings into the docs' looping videos, using ffmpeg."""
import subprocess
from dataclasses import dataclass
from pathlib import Path

_PANEL_HEIGHT = 104
_FADE_S = 0.3
_FPS = 15
_BOLD_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_REGULAR_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_ENCODING = (
    "-c:v", "libx264", "-preset", "veryslow", "-crf", "30", "-pix_fmt", "yuv420p",
    "-movflags", "+faststart", "-an",
)


@dataclass(frozen=True)
class Reel:
    """One recording and the panel explaining it."""

    clip: Path
    seconds: float
    title: str
    text: str


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def encode(source: Path, output: Path) -> None:
    """Compress a raw recording into a video for the site."""
    output.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-i", str(source), *_ENCODING, str(output))


def _captioned(reel: Reel, index: int, width: int, workdir: Path) -> Path:
    """`reel` under its panel: a title, one sentence, and a bar showing the time left."""
    title = workdir / f"title{index}.txt"
    text = workdir / f"text{index}.txt"
    title.write_text(reel.title)
    text.write_text(reel.text)
    output = workdir / f"captioned{index}.mp4"
    fade_out = reel.seconds - _FADE_S
    graph = (
        f"[0:v]pad=iw:ih+{_PANEL_HEIGHT}:0:{_PANEL_HEIGHT}:color=0x16181d,"
        f"drawbox=x=0:y=0:w=8:h={_PANEL_HEIGHT}:color=0x971b2f:t=fill,"
        f"drawtext=fontfile={_BOLD_FONT}:textfile={title}:x=32:y=20:fontsize=28:fontcolor=white,"
        f"drawtext=fontfile={_REGULAR_FONT}:textfile={text}:x=32:y=62:fontsize=18:fontcolor=0xd1d5db[base];"
        f"color=c=0xffb500:s={width}x4:r={_FPS}[bar];"
        f"[base][bar]overlay=x='-W+W*t/{reel.seconds}':y={_PANEL_HEIGHT - 4}:shortest=1,"
        f"fade=t=in:st=0:d={_FADE_S},fade=t=out:st={fade_out}:d={_FADE_S}[out]"
    )
    _ffmpeg(
        "-t", str(reel.seconds), "-i", str(reel.clip), "-filter_complex", graph, "-map", "[out]",
        "-r", str(_FPS), "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", str(output),
    )
    return output


def join(reels: list[Reel], width: int, output: Path, workdir: Path) -> None:
    """Play `reels` one after the other, each under its own panel."""
    parts = [_captioned(reel, index, width, workdir) for index, reel in enumerate(reels)]
    playlist = workdir / "playlist.txt"
    playlist.write_text("".join(f"file '{part}'\n" for part in parts))
    output.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-f", "concat", "-safe", "0", "-i", str(playlist), *_ENCODING, str(output))
    # Each reel fades in from black, so the first frame can't stand in while the video loads.
    _ffmpeg("-ss", "2", "-i", str(output), "-frames:v", "1", "-q:v", "4", str(output.with_suffix(".jpg")))
