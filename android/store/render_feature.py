"""Developer-only graphic render from the existing brand icon (requires Pillow)."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]


def main():
    canvas = Image.new("RGB", (1024, 500), "#0f1b22")
    draw = ImageDraw.Draw(canvas)
    regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    icon = Image.open(ROOT / "brand/wowpriced-icon-512.png").convert("RGBA")
    icon = icon.resize((244, 244), Image.Resampling.LANCZOS)
    canvas.paste(icon, (62, 128), icon)
    for xy, text, face, size, color in (
        ((350, 82), "wowpriced", bold, 54, "#60ddca"),
        ((350, 164), "Is it a good deal?", bold, 38, "#f3f7f8"),
        ((350, 235), "Share a product link.", regular, 29, "#f3f7f8"),
        ((350, 280), "Check our verified records.", regular, 29, "#f3f7f8"),
    ):
        draw.text(xy, text, font=ImageFont.truetype(face, size), fill=color)
    draw.rounded_rectangle((350, 359, 642, 418), radius=16, fill="#ffc94d")
    draw.text((375, 371), "Deal Check", font=ImageFont.truetype(bold, 27), fill="#0f1b22")
    canvas.save(Path(__file__).with_name("feature-graphic.png"), optimize=True)


if __name__ == "__main__":
    main()
