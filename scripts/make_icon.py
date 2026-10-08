"""Draws the app icon (spectrum bars on a blue-violet gradient) -> musicplayer/resources/icon.png + icon.ico."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "musicplayer" / "resources"
SIZE = 512


def gradient(size: int, c1: tuple[int, int, int], c2: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size - 2)
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return img


def main() -> None:
    bg = gradient(SIZE, (47, 124, 246), (108, 92, 231))          # the app's accent colours
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=SIZE // 4, fill=255)
    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(icon)
    heights = [0.30, 0.55, 0.80, 0.62, 0.42, 0.68, 0.36]          # a frozen spectrum
    n = len(heights)
    pad, gap = SIZE * 0.16, SIZE * 0.035
    bw = (SIZE - 2 * pad - gap * (n - 1)) / n
    base = SIZE * 0.80
    for i, h in enumerate(heights):
        x0 = pad + i * (bw + gap)
        top = base - h * SIZE * 0.60
        d.rounded_rectangle((x0, top, x0 + bw, base), radius=bw / 2, fill=(255, 255, 255, 245))
    # a small play triangle above the tallest bar hints at the player
    cx = pad + 2 * (bw + gap) + bw / 2
    ty = base - heights[2] * SIZE * 0.60 - SIZE * 0.07
    d.polygon([(cx - SIZE * 0.045, ty - SIZE * 0.05), (cx - SIZE * 0.045, ty + SIZE * 0.05), (cx + SIZE * 0.06, ty)],
              fill=(255, 255, 255, 255))
    OUT.mkdir(parents=True, exist_ok=True)
    icon.save(OUT / "icon.png")
    icon.save(OUT / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("icon written to", OUT)


if __name__ == "__main__":
    main()
