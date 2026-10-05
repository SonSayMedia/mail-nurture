"""Sinh icon app 'Mail Nurture': nền xanh gradient bo góc + phong bì thư + mầm cây."""
from __future__ import annotations

import os
from PIL import Image, ImageDraw, ImageFilter

SS = 4
S = 256 * SS
OUT = os.path.join(os.path.dirname(__file__), "assets")
os.makedirs(OUT, exist_ok=True)


def lerp(a, b, t):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def build() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Nền gradient xanh, bo góc
    top, bot = (48, 194, 130), (13, 130, 86)
    grad = Image.new("RGB", (S, S))
    px = grad.load()
    for y in range(S):
        c = lerp(top, bot, y / S)
        for x in range(S):
            px[x, y] = c
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    # Kích thước phong bì
    ew, eh = int(S * 0.58), int(S * 0.40)
    ex, ey = (S - ew) // 2, int(S * 0.37)
    er = int(S * 0.045)

    # Bóng đổ mềm dưới phong bì
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [ex, ey + int(S * 0.03), ex + ew, ey + eh + int(S * 0.03)], radius=er, fill=(0, 50, 34, 110))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(int(S * 0.022))))

    d = ImageDraw.Draw(img)
    # Thân phong bì (trắng)
    d.rounded_rectangle([ex, ey, ex + ew, ey + eh], radius=er, fill=(255, 255, 255, 255))
    # Nếp gấp thư (chữ V)
    lw = int(S * 0.016)
    fc = (188, 224, 208)
    d.line([ex + er, ey + int(eh * 0.12), S // 2, ey + int(eh * 0.55)], fill=fc, width=lw, joint="curve")
    d.line([ex + ew - er, ey + int(eh * 0.12), S // 2, ey + int(eh * 0.55)], fill=fc, width=lw, joint="curve")

    # Mầm cây 2 lá mọc lên phía trên phong bì
    cx = S // 2
    base_y = ey + int(S * 0.015)
    top_y = ey - int(S * 0.205)
    d.line([cx, base_y, cx, top_y + int(S * 0.02)], fill=(255, 255, 255, 255),
           width=int(S * 0.028), joint="curve")

    def leaf(lcx, lcy, angle):
        w, h = int(S * 0.25), int(S * 0.105)     # lá dài, thon -> nhọn giống lá thật
        lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(lay).ellipse([0, 0, w - 1, h - 1], fill=(255, 255, 255, 255))
        lay = lay.rotate(angle, expand=True, resample=Image.BICUBIC)
        img.alpha_composite(lay, (int(lcx - lay.width / 2), int(lcy - lay.height / 2)))

    # Hai lá tách rõ, chếch lên hai bên
    leaf(cx - int(S * 0.085), top_y + int(S * 0.055), 52)
    leaf(cx + int(S * 0.085), top_y + int(S * 0.055), -52)
    return img


def main():
    img = build().resize((256, 256), Image.LANCZOS)
    ico = os.path.join(OUT, "app_icon.ico")
    img.save(ico, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    build().resize((512, 512), Image.LANCZOS).save(os.path.join(OUT, "app_icon_preview.png"))
    print("Da tao:", ico)


if __name__ == "__main__":
    main()
