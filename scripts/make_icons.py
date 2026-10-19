"""Genera le icone PNG della PWA (richiede Pillow: `pip install pillow`).

Uso: python scripts/make_icons.py

Le icone sono gia' versionate in `static/icons/`: questo script serve solo se
vuoi cambiarle (colori, forma, dimensioni).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

WEB = Path(__file__).resolve().parent.parent / "static"
ICONS = WEB / "icons"

BG_TOP = (16, 24, 34)
BG_BOTTOM = (10, 14, 19)
ACCENT = (110, 231, 183)
BLUE = (96, 165, 250)


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def gradient(size: int) -> Image.Image:
    image = Image.new("RGB", (size, size), BG_TOP)
    draw = ImageDraw.Draw(image)
    for y in range(size):
        blend = y / max(1, size - 1)
        color = tuple(
            int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * blend) for i in range(3)
        )
        draw.line([(0, y), (size, y)], fill=color)
    return image


def draw_car(draw: ImageDraw.ImageDraw, size: int, scale: float) -> None:
    """Auto stilizzata centrata, dimensionata in proporzione al canvas."""
    w = size * scale
    h = w * 0.52
    left = (size - w) / 2
    top = (size - h) / 2 + size * 0.04
    body_h = h * 0.58

    # Corpo
    draw.rounded_rectangle(
        [left, top + h - body_h, left + w, top + h], radius=int(body_h * 0.35), fill=ACCENT
    )
    # Abitacolo
    cabin = [
        (left + w * 0.24, top + h - body_h),
        (left + w * 0.4, top),
        (left + w * 0.66, top),
        (left + w * 0.82, top + h - body_h),
    ]
    draw.polygon(cabin, fill=ACCENT)
    # Finestrino
    window = [
        (left + w * 0.31, top + h - body_h - h * 0.03),
        (left + w * 0.43, top + h * 0.14),
        (left + w * 0.62, top + h * 0.14),
        (left + w * 0.74, top + h - body_h - h * 0.03),
    ]
    draw.polygon(window, fill=BG_TOP)
    # Ruote
    radius = h * 0.2
    for cx in (left + w * 0.24, left + w * 0.76):
        cy = top + h
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=BLUE)
        inner = radius * 0.45
        draw.ellipse([cx - inner, cy - inner, cx + inner, cy + inner], fill=BG_BOTTOM)


def make_icon(size: int, path: Path, *, scale: float, rounded: bool) -> None:
    image = gradient(size)
    draw = ImageDraw.Draw(image)
    draw_car(draw, size, scale)
    if rounded:
        mask = rounded_mask(size, int(size * 0.22))
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(image, (0, 0), mask)
        canvas.save(path)
    else:
        image.save(path)


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    make_icon(192, ICONS / "icon-192.png", scale=0.62, rounded=True)
    make_icon(512, ICONS / "icon-512.png", scale=0.62, rounded=True)
    # Maskable: soggetto piu' piccolo per rientrare nella zona sicura.
    make_icon(512, ICONS / "icon-maskable-512.png", scale=0.44, rounded=False)
    print(f"Icone create in {ICONS}")


if __name__ == "__main__":
    main()
