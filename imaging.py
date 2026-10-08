import subprocess


def pixelate(image: bytes, size: int, saturate: int) -> bytes:
    """Any ImageMagick-readable image -> size*size*3 raw RGB bytes."""
    # -scale averages pixels by area, which gives the most faithful colors at 32x32.
    cmd = [
        "magick", "-", "-colorspace", "sRGB", "-alpha", "remove",
        "-modulate", f"100,{saturate}",
        "-scale", f"{size}x{size}!", "-depth", "8", "rgb:-",
    ]
    rgb = subprocess.run(cmd, input=image, capture_output=True, check=True).stdout
    if len(rgb) != size * size * 3:
        raise ValueError(f"unexpected image size: {len(rgb)} bytes")
    return rgb
