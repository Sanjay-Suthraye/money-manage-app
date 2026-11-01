"""Regenerate the app icons and the Play Store graphics.

Run on the Mac (font paths are macOS-specific); the outputs are committed
artifacts:
    ~/miniconda3/envs/money/bin/python scripts/make_icons.py

Three things here are load-bearing:

1. The font must actually contain U+20B9. Arial Bold does NOT — it silently
   drew .notdef, so every committed icon was a blank tofu box. assert_glyph()
   now fails the build instead, because a missing glyph is invisible in a
   filename and obvious only once the app is on a home screen.

2. Alpha, where the two consumers want opposite things:
     * web/launcher icons and Play's 512x512 store icon must be 32-bit PNGs
       *with* an alpha channel (colour type 6) — Play rejects a 24-bit icon —
       yet fully opaque and full-bleed. "Has alpha" is not "is transparent";
       a genuinely transparent store icon renders as a black square.
     * Play's 1024x500 feature graphic must have *no* transparency, so it is
       written in RGB (colour type 2).

3. Bubblewrap derives every Android density, including the adaptive icon, from
   the maskable PNGs, so those keep the glyph inside the 80% safe zone.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Helvetica Neue Bold, index 1 of the collection. Chosen over Arial Bold
# because it has U+20B9; see assert_glyph().
FONT = ("/System/Library/Fonts/HelveticaNeue.ttc", 1)
ACCENT = "#3346d3"  # --accent (light) from frontend/css/tokens.css
ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "frontend"
STORE = ROOT / "store"

RUPEE = "₹"
WORDMARK = "Money Manager"
TAGLINE = "Spend. Split. See where it went."


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT[0], size, index=FONT[1])


def assert_glyph(ch: str) -> None:
    """Refuse to generate anything if the font draws .notdef for `ch`.

    A font without the codepoint renders the same tofu box as a private-use
    codepoint that is guaranteed absent, so comparing the two pixel buffers
    detects it without a font-table parser.
    """
    f = font(48)

    def render(s: str) -> bytes:
        im = Image.new("L", (100, 100), 0)
        ImageDraw.Draw(im).text((10, 10), s, font=f, fill=255)
        return im.tobytes()

    if render(ch) in (render(""), render(" ")):
        raise SystemExit(
            f"{FONT[0]}#{FONT[1]} has no glyph for U+{ord(ch):04X} ({ch!r}) — "
            "it would render as a blank box. Pick another font."
        )


def _draw_centered(d: ImageDraw.ImageDraw, text: str, f, cx: float, cy: float) -> None:
    """Place `text` so its inked bounding box is centred on (cx, cy)."""
    l, t, r, b = d.textbbox((0, 0), text, font=f)
    d.text((cx - (r - l) / 2 - l, cy - (b - t) / 2 - t), text, font=f, fill="white")


def icon(size: int, glyph_scale: float, out: Path) -> None:
    """Opaque accent square with a white ₹, saved RGBA (alpha = 255 everywhere)."""
    img = Image.new("RGBA", (size, size), ACCENT)   # PIL fills alpha=255
    _draw_centered(ImageDraw.Draw(img), RUPEE, font(int(size * glyph_scale)),
                   size / 2, size / 2)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"wrote {out.relative_to(ROOT)} ({size}x{size}, RGBA opaque)")


def feature_graphic(out: Path) -> None:
    """1024x500 listing banner, RGB so it carries no alpha channel at all.

    Play crops and overlays the edges on some surfaces, so the glyph and the
    wordmark are measured and centred as one group rather than placed at fixed
    offsets.
    """
    w, h = 1024, 500
    img = Image.new("RGB", (w, h), ACCENT)
    d = ImageDraw.Draw(img)

    f_mark, f_word, f_tag = font(230), font(76), font(31)
    gap = 44

    def width(text: str, f) -> float:
        l, _, r, _ = d.textbbox((0, 0), text, font=f)
        return r - l

    text_w = max(width(WORDMARK, f_word), width(TAGLINE, f_tag))
    group_w = width(RUPEE, f_mark) + gap + text_w
    left = (w - group_w) / 2

    _draw_centered(d, RUPEE, f_mark, left + width(RUPEE, f_mark) / 2, h / 2)
    text_cx = left + width(RUPEE, f_mark) + gap + text_w / 2
    _draw_centered(d, WORDMARK, f_word, text_cx, h / 2 - 34)
    _draw_centered(d, TAGLINE, f_tag, text_cx, h / 2 + 46)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"wrote {out.relative_to(ROOT)} ({w}x{h}, RGB no alpha)")


if __name__ == "__main__":
    assert_glyph(RUPEE)
    icon(180, 0.62, WEB / "icon-180.png")           # apple-touch-icon
    icon(192, 0.62, WEB / "icon-192.png")
    icon(512, 0.62, WEB / "icon-512.png")
    icon(192, 0.42, WEB / "icon-192-maskable.png")  # glyph inside the safe zone
    icon(512, 0.42, WEB / "icon-512-maskable.png")
    icon(512, 0.62, STORE / "icon-512-play.png")    # Play listing icon
    feature_graphic(STORE / "feature-graphic-1024x500.png")
