"""PDF export: combine selected screenshots into a single PDF, in the
given order. Uses Pillow only -- no extra PDF dependency needed,
since Pillow can save a list of RGB images as a multi-page PDF
directly.
"""

from pathlib import Path

from PIL import Image


class EmptyExportError(ValueError):
    """Raised when asked to export zero screenshots, so the UI can
    show a clear message instead of silently doing nothing or
    producing a blank file."""


def export_pdf(image_paths: list[Path], output_path: Path) -> Path:
    if not image_paths:
        raise EmptyExportError("Cannot export a PDF with no screenshots selected.")

    images = [Image.open(p).convert("RGB") for p in image_paths]
    first, rest = images[0], images[1:]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    first.save(output_path, format="PDF", save_all=True, append_images=rest)
    return output_path
