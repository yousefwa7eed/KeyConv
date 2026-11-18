# tools/make_icon.py — يولّد أيقونة KeyConv (assets/keyconv.ico) بكل المقاسات.
#
# التصميم: مربّع بزوايا دائرية بتدرّج أخضر/أزرق، وسهمان أبيضان متعاكسان
# (عربي <-> إنجليزي). الشكل بسيط عمداً ليظل واضحاً حتى في مقاس 16×16.

import os

from PIL import Image, ImageDraw

SIZE = 1024
TOP = (16, 185, 170)      # #10B9AA
BOTTOM = (37, 99, 235)    # #2563EB
WHITE = (255, 255, 255, 255)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
ICO_SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
             (48, 48), (64, 64), (128, 128), (256, 256)]


def vertical_gradient(size, top, bottom):
    image = Image.new("RGB", (size, size), top)
    draw = ImageDraw.Draw(image)
    for y in range(size):
        t = y / (size - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
        draw.line([(0, y), (size, y)], fill=color)
    return image


def rounded_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255
    )
    return mask


def build():
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(
        vertical_gradient(SIZE, TOP, BOTTOM).convert("RGBA"),
        (0, 0),
        rounded_mask(SIZE, 226),
    )
    draw = ImageDraw.Draw(canvas)

    # حدّ داخلي خفيف يعطي إحساس العمق
    draw.rounded_rectangle(
        [34, 34, SIZE - 34, SIZE - 34],
        radius=196, outline=(255, 255, 255, 42), width=9,
    )

    # السهم العلوي: إلى اليمين
    draw.line([(250, 360), (690, 360)], fill=WHITE, width=126)
    draw.polygon([(668, 232), (892, 360), (668, 488)], fill=WHITE)

    # السهم السفلي: إلى اليسار
    draw.line([(774, 664), (334, 664)], fill=WHITE, width=126)
    draw.polygon([(356, 536), (132, 664), (356, 792)], fill=WHITE)

    return canvas


def main():
    os.makedirs(ASSETS, exist_ok=True)
    icon = build()
    ico_path = os.path.join(ASSETS, "keyconv.ico")
    icon.save(ico_path, format="ICO", sizes=ICO_SIZES)
    icon.resize((512, 512), Image.LANCZOS).save(
        os.path.join(ASSETS, "keyconv.png"), format="PNG"
    )
    print("wrote", ico_path)
    print("wrote", os.path.join(ASSETS, "keyconv.png"))


if __name__ == "__main__":
    main()
