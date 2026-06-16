# -*- coding: utf-8 -*-
"""
Generate the app icon (static/icon.ico) used by both the exe and the tray.

Brand: sky-blue gradient rounded square + bold white lightning bolt.
Run from project root:  python tools/make_icon.py
Outputs: static/icon.ico  (multi-size) and static/icon_preview.png (for review)
"""
import os
from PIL import Image, ImageDraw

S = 512  # supersample canvas (downscaled to 256 for crisp edges)

C1 = (14, 165, 233)   # #0ea5e9  sky
C2 = (59, 130, 246)   # #3b82f6  blue


def _lerp(a, b, t):
    return int(a + (b - a) * t)


def _diagonal_gradient(size, c1, c2):
    img = Image.new('RGB', (size, size))
    px = img.load()
    denom = 2 * (size - 1)
    for y in range(size):
        for x in range(size):
            t = (x + y) / denom
            px[x, y] = (_lerp(c1[0], c2[0], t),
                        _lerp(c1[1], c2[1], t),
                        _lerp(c1[2], c2[2], t))
    return img


def main():
    out_dir = 'static'
    os.makedirs(out_dir, exist_ok=True)

    bg = _diagonal_gradient(S, C1, C2)

    # rounded-square mask
    mask = Image.new('L', (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255
    )

    result = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    result.paste(bg, (0, 0), mask)

    # bold lightning bolt (normalized points -> canvas)
    pts = [(0.54, 0.08), (0.26, 0.55), (0.45, 0.55),
           (0.40, 0.92), (0.74, 0.41), (0.55, 0.41)]
    poly = [(x * S, y * S) for x, y in pts]

    # subtle shadow for depth, then the white bolt
    shadow = [(px + S * 0.012, py + S * 0.012) for px, py in poly]
    ImageDraw.Draw(result).polygon(shadow, fill=(10, 40, 80, 90))
    ImageDraw.Draw(result).polygon(poly, fill=(255, 255, 255, 255))

    icon = result.resize((256, 256), Image.LANCZOS)
    ico_path = os.path.join(out_dir, 'icon.ico')
    icon.save(ico_path, sizes=[(256, 256), (128, 128), (64, 64),
                               (48, 48), (32, 32), (16, 16)])
    png_path = os.path.join(out_dir, 'icon_preview.png')
    icon.save(png_path)
    print('[OK] wrote', ico_path, 'and', png_path)


if __name__ == '__main__':
    main()
