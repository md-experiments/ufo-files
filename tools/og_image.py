"""Draw the social-preview image (static/og.png) shown when a page is shared.

Run ``python tools/og_image.py`` after changing it; the PNG is committed."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "ufo" / "web" / "static" / "og.png"
W, H = 1200, 630
BG, INK, BLUE, DOME, MUTED = (16, 18, 22), (240, 238, 232), (42, 120, 214), (28, 92, 171), (150, 152, 160)


def font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "LiberationSans-Bold.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def main() -> None:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # a faint grid, like the charts on the site
    for x in range(0, W, 60):
        d.line([(x, 0), (x, H)], fill=(24, 26, 31))
    for y in range(0, H, 60):
        d.line([(0, y), (W, y)], fill=(24, 26, 31))
    # the saucer from the favicon, large, on the right
    cx, cy = 900, 330
    d.ellipse([cx - 220, cy - 30, cx + 220, cy + 110], fill=BLUE)
    d.chord([cx - 110, cy - 130, cx + 110, cy + 60], 180, 360, fill=DOME)
    d.ellipse([cx - 150, cy + 150, cx + 150, cy + 175], fill=(30, 34, 42))
    # wordmark and tagline
    d.text((80, 190), "UFO FILES", font=font(96), fill=INK)
    d.text((84, 310), "What the U.S. government has released", font=font(34), fill=INK)
    d.text((84, 356), "in its declassified UFO / UAP files", font=font(34), fill=INK)
    d.text((84, 440), "Releases · Patterns · Events · full-text search", font=font(26), fill=MUTED)
    im.save(OUT, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
