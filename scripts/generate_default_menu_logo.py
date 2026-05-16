"""Generate app/resources/default_menu_logo.png (bundled iPXE menu logo). Run after changing copy."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "app" / "resources" / "default_menu_logo.png"

# Match menu theme (slate blue)
BG = (22, 42, 74)
FG = (226, 232, 240)  # slate-200
FG_DIM = (148, 163, 184)  # slate-400

LINES: list[tuple[str, bool]] = [
    ("Customizable logo", True),
    ("Settings · iPXE menu image", False),
]


def _try_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
    ]
    for p in candidates:
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size=size)
            except OSError:
                pass
    return ImageFont.load_default()


def main() -> None:
    W, H = 440, 112
    img = Image.new("RGBA", (W, H), BG + (255,))
    draw = ImageDraw.Draw(img)

    entries: list[tuple[str, ImageFont.ImageFont, tuple[int, int, int]]] = []
    for text, is_title in LINES:
        f = _try_font(20 if is_title else 15)
        color = FG if is_title else FG_DIM
        entries.append((text, f, color))

    heights: list[int] = []
    widths: list[int] = []
    for text, f, _ in entries:
        b = draw.textbbox((0, 0), text, font=f)
        heights.append(b[3] - b[1])
        widths.append(b[2] - b[0])

    gap = 6
    total_h = sum(heights) + gap * (len(entries) - 1)
    y = (H - total_h) // 2

    for (text, f, color), w, h in zip(entries, widths, heights, strict=False):
        x = (W - w) // 2
        draw.text((x, y), text, font=f, fill=color)
        y += h + gap

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print("Wrote", OUT)


if __name__ == "__main__":
    main()
