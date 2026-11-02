"""Export selected screenshots as individual image files, preserving
order via a sequence-numbered filename prefix.
"""

import shutil
from pathlib import Path


def export_images(image_paths: list[Path], output_dir: Path, fmt: str = "png") -> list[Path]:
    fmt = fmt.lower()
    if fmt not in ("png", "jpg", "jpeg"):
        raise ValueError(f"Unsupported export format: {fmt}")

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for i, src in enumerate(image_paths, start=1):
        dest = output_dir / f"slide_{i:04d}.{fmt}"
        if fmt == "png" and src.suffix.lower() == ".png":
            shutil.copyfile(src, dest)
        else:
            from PIL import Image

            Image.open(src).convert("RGB").save(dest)
        written.append(dest)

    return written
