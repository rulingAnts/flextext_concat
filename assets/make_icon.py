#!/usr/bin/env python3
"""
Generate the app icon at every size the platforms need.

The mark: three short text lines converging into one longer line — the app
takes several interlinear texts and produces a single combined one. Kept to
solid shapes with heavy contrast because the size that actually matters is
16 px in a Dock or file listing, where anything finer turns to mush.

Run:  python3 assets/make_icon.py
Then: iconutil -c icns assets/icon.iconset -o assets/icon.icns
"""
from pathlib import Path

from PIL import Image, ImageDraw

TEAL = (15, 118, 110)        # matches the docs site accent
TEAL_DARK = (17, 94, 89)
PAPER = (248, 250, 249)
ACCENT = (250, 204, 21)      # the combined line

HERE = Path(__file__).parent


def draw(size: int) -> Image.Image:
    # Supersample, then downscale: cheap, and keeps edges clean at 16 px.
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Solid ground with a hairline edge. An earlier two-tone version put a
    # hard seam straight through the third line, which looked like a defect.
    r = int(s * 0.22)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=TEAL,
                        outline=TEAL_DARK, width=max(1, int(s * 0.02)))

    # Three source lines on the left, staggered.
    line_h = int(s * 0.075)
    left = int(s * 0.17)
    widths = (0.34, 0.28, 0.31)
    tops = (0.22, 0.40, 0.58)
    for w, t in zip(widths, tops):
        y = int(s * t)
        d.rounded_rectangle([left, y, left + int(s * w), y + line_h],
                            radius=line_h // 2, fill=PAPER)

    # Converging strokes into the single combined line.
    mid_x = int(s * 0.60)
    out_y = int(s * 0.40) + line_h // 2
    for t in tops:
        y = int(s * t) + line_h // 2
        d.line([(left + int(s * 0.30), y), (mid_x, out_y)],
               fill=PAPER, width=max(2, int(s * 0.018)))

    # The combined line, in the accent colour so the "one output" reads fast.
    out_left = mid_x
    out_w = int(s * 0.24)
    d.rounded_rectangle([out_left, out_y - line_h // 2,
                         out_left + out_w, out_y + line_h // 2],
                        radius=line_h // 2, fill=ACCENT)

    return img.resize((size, size), Image.LANCZOS)


def main():
    iconset = HERE / "icon.iconset"
    iconset.mkdir(exist_ok=True)

    # macOS iconset naming, including @2x retina variants.
    for base in (16, 32, 128, 256, 512):
        draw(base).save(iconset / f"icon_{base}x{base}.png")
        draw(base * 2).save(iconset / f"icon_{base}x{base}@2x.png")

    draw(512).save(HERE / "icon.png")            # Linux / AppImage
    # .ico wants several sizes in one file; Windows picks per context.
    draw(256).save(HERE / "icon.ico",
                   sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                          (64, 64), (128, 128), (256, 256)])
    print(f"wrote {iconset}/ (10 png), icon.png, icon.ico")


if __name__ == "__main__":
    main()
