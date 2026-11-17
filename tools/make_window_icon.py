"""Regenerate testudo/tui/assets/window_icon.bin from docs/logo.svg (dev only, not installed).

Renders the shield with headless Chrome on a transparent background, pads
it to a square, scales it to each size in SIZES with Pillow, and writes
the X11 `_NET_WM_ICON` layout -- per size: width, height, then one ARGB
cardinal per pixel, row by row -- as little-endian uint32, zlib-compressed.
`testudo.tui.window_icon` loads it with the standard library only.

Usage: python3 tools/make_window_icon.py   (needs google-chrome and Pillow)
"""
from __future__ import annotations

import pathlib
import struct
import subprocess
import tempfile
import zlib

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
SVG = ROOT / "docs" / "logo.svg"
OUT = ROOT / "testudo" / "tui" / "assets" / "window_icon.bin"
SIZES = (16, 24, 32, 48, 64, 128)
RENDER_HEIGHT = 860  # the SVG's own height: 580 x 860


def render_svg(directory: pathlib.Path) -> Image.Image:
    """The shield as an RGBA image at its native size, transparent around it."""
    page = directory / "logo.html"
    page.write_text(
        f'<html><body style="margin:0;background:transparent">'
        f'<img src="{SVG.as_uri()}" style="display:block"></body></html>'
    )
    shot = directory / "logo.png"
    subprocess.run(
        [
            "google-chrome",
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--default-background-color=00000000",
            f"--screenshot={shot}",
            f"--window-size=580,{RENDER_HEIGHT}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    return Image.open(shot).convert("RGBA")


def square(image: Image.Image) -> Image.Image:
    """`image` centered on a transparent square as tall as it is."""
    side = max(image.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    return canvas


def cardinals(image: Image.Image) -> list[int]:
    """width, height, then each pixel as 0xAARRGGBB."""
    values = [image.width, image.height]
    for red, green, blue, alpha in image.getdata():
        values.append(alpha << 24 | red << 16 | green << 8 | blue)
    return values


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        shield = square(render_svg(pathlib.Path(directory)))
    values: list[int] = []
    for size in SIZES:
        values += cardinals(shield.resize((size, size), Image.LANCZOS))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(zlib.compress(struct.pack(f"<{len(values)}I", *values), 9))
    print(f"{OUT.relative_to(ROOT)}: {len(SIZES)} sizes, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
