"""Regenerate the app icons: accent background + white ₹ glyph.

Run on the Mac (font path is macOS-specific); icons are committed artifacts:
    ~/miniconda3/envs/money/bin/python scripts/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"  # has U+20B9 ₹
ACCENT = "#2a78d6"  # --accent (light) from frontend/css/tokens.css
OUT = Path(__file__).resolve().parent.parent / "frontend"


def make(size: int, glyph_scale: float, name: str) -> None:
    img = Image.new("RGB", (size, size), ACCENT)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT, int(size * glyph_scale))
    box = d.textbbox((0, 0), "₹", font=font)
    d.text(
        ((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]),
        "₹", font=font, fill="white",
    )
    img.save(OUT / name)
    print(f"wrote {name} ({size}x{size})")


if __name__ == "__main__":
    make(180, 0.62, "icon-180.png")
    make(192, 0.62, "icon-192.png")
    make(512, 0.62, "icon-512.png")
    make(512, 0.42, "icon-512-maskable.png")  # glyph inside the maskable safe zone
