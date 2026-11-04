import numpy as np
import pytest
from PIL import Image

from sniply.export.image_export import export_images
from sniply.export.pdf_export import EmptyExportError, export_pdf


def _make_png(path, color):
    img = Image.fromarray(np.full((20, 20, 3), color, dtype=np.uint8))
    img.save(path)
    return path


def test_export_pdf_creates_multi_page_file(tmp_path):
    p1 = _make_png(tmp_path / "a.png", 255)
    p2 = _make_png(tmp_path / "b.png", 0)

    out = export_pdf([p1, p2], tmp_path / "out.pdf")

    assert out.exists()
    # Pillow can WRITE multi-page PDFs but can't read them back as an
    # Image (no PDF decoder), so verify page count by counting page
    # objects in the raw PDF bytes instead.
    raw = out.read_bytes()
    assert raw.startswith(b"%PDF")
    # "/Type /Page" is a substring of "/Type /Pages", so match with a
    # trailing newline to count only actual page objects.
    assert raw.count(b"/Type /Page\n") == 2


def test_export_pdf_rejects_empty_list(tmp_path):
    with pytest.raises(EmptyExportError):
        export_pdf([], tmp_path / "out.pdf")


def test_export_images_preserves_order(tmp_path):
    p1 = _make_png(tmp_path / "a.png", 255)
    p2 = _make_png(tmp_path / "b.png", 0)

    written = export_images([p1, p2], tmp_path / "out")

    assert [p.name for p in written] == ["slide_0001.png", "slide_0002.png"]
