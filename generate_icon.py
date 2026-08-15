from pathlib import Path

from PIL import Image, ImageOps


def main():
    target = Path(__file__).parent / "assets"
    source = target / "fitpeek.png"
    if not source.is_file():
        raise FileNotFoundError(f"Source icon not found: {source}")
    with Image.open(source) as original:
        image = ImageOps.contain(original.convert("RGBA"), (896, 896), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (1024, 1024), (255, 255, 255, 255))
    canvas.alpha_composite(image, ((1024 - image.width) // 2, (1024 - image.height) // 2))
    canvas.save(
        target / "fitpeek.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(target / "fitpeek.ico")


if __name__ == "__main__":
    main()
