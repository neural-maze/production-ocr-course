import io

from PIL import Image

from ocr_pipeline.engine.parser import parse_multi_page_output
from ocr_pipeline.engine.pdf import detect_document_type, rasterize_document_to_pages


def test_parse_unlimited_ocr_2_multi_page() -> None:
    raw = (
        "<PAGE>\n"
        "<BLOCK>title[48, 85, 388, 127]<FORK>Quarterly Financial Overview\n"
        "<BLOCK>text[48, 150, 910, 320]<FORK>Revenue grew by 24% year-over-year.\n"
        "<PAGE>\n"
        "<BLOCK>section_header[50, 60, 420, 95]<FORK>Regional Breakdown\n"
        "<BLOCK>table[50, 120, 900, 450]<FORK>| Region | Q3 |\n| --- | --- |\n| EMEA | $12M |<|eos|>"
    )

    pages = parse_multi_page_output(raw, expected_pages=2)
    assert len(pages) == 2

    assert pages[0].page_offset == 0
    assert len(pages[0].bboxes) == 2
    assert pages[0].bboxes[0].label == "title"
    assert pages[0].bboxes[0].xmin == 48
    assert pages[0].bboxes[0].ymax == 127
    assert "# Quarterly Financial Overview" in pages[0].markdown
    assert "Revenue grew by 24%" in pages[0].markdown

    assert pages[1].page_offset == 1
    assert len(pages[1].bboxes) == 2
    assert "## Regional Breakdown" in pages[1].markdown
    assert "| EMEA | $12M |" in pages[1].markdown


def test_parse_unlimited_ocr_v1_fallback() -> None:
    raw = "<|det|>title[[10, 20, 300, 50]]<|/det|><|ref|>Hello World<|/ref|>"
    pages = parse_multi_page_output(raw, expected_pages=1)
    assert len(pages) == 1
    assert len(pages[0].bboxes) == 1
    assert pages[0].bboxes[0].label == "title"
    assert pages[0].markdown == "Hello World"


def test_detect_and_rasterize_single_png() -> None:
    img = Image.new("RGB", (320, 240), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    mime, is_pdf = detect_document_type(png_bytes)
    assert mime == "image/png"
    assert is_pdf is False

    out_mime, pages = rasterize_document_to_pages(png_bytes, max_pages=10, dpi=150)
    assert out_mime == "image/png"
    assert len(pages) == 1
    assert pages[0].width == 320
    assert pages[0].height == 240
    assert pages[0].image_data_uri.startswith("data:image/png;base64,")

