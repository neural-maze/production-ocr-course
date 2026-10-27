import base64
import io
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image


@dataclass
class RasterizedPage:
    page_number: int
    width: int
    height: int
    png_bytes: bytes
    image_data_uri: str


def detect_document_type(file_bytes: bytes) -> tuple[str, bool]:
    """Detects MIME type and whether the file is a PDF from raw magic bytes."""
    if len(file_bytes) < 4:
        raise ValueError("Uploaded file is too small or empty.")

    if file_bytes.startswith(b"%PDF-"):
        return "application/pdf", True
    if file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", False
    if file_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", False
    if len(file_bytes) >= 12 and file_bytes.startswith(b"RIFF") and file_bytes[8:12] == b"WEBP":
        return "image/webp", False
    if file_bytes.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff", False
    if file_bytes.startswith(b"BM"):
        return "image/bmp", False

    raise ValueError(
        "Unsupported document format. Please supply a valid PDF, PNG, JPEG, WebP, TIFF, or BMP file."
    )


def clean_and_decode_base64(raw_b64: str) -> bytes:
    """Strips optional data URI prefix and decodes base64 string to bytes."""
    clean = raw_b64
    if ";base64," in clean:
        clean = clean.split(";base64,", 1)[1]
    elif "," in clean and clean.startswith("data:"):
        clean = clean.split(",", 1)[1]
    clean = clean.strip().replace("\r", "").replace("\n", "").replace(" ", "")
    return base64.b64decode(clean)


def rasterize_document_to_pages(
    file_bytes: bytes,
    max_pages: int = 40,
    dpi: int = 150,
) -> tuple[str, list[RasterizedPage]]:
    """
    Validates magic bytes and converts a PDF or single image into an ordered list
    of 150-DPI PNG pages with dimensions and base64 Data URIs.
    """
    mime_type, is_pdf = detect_document_type(file_bytes)

    if not is_pdf:
        with Image.open(io.BytesIO(file_bytes)) as img:
            rgb_img = img.convert("RGB")
            width, height = rgb_img.size
            buf = io.BytesIO()
            rgb_img.save(buf, format="PNG")
            png_bytes = buf.getvalue()

        b64_str = base64.b64encode(png_bytes).decode("ascii")
        return mime_type, [
            RasterizedPage(
                page_number=1,
                width=width,
                height=height,
                png_bytes=png_bytes,
                image_data_uri=f"data:image/png;base64,{b64_str}",
            )
        ]

    pdf = pdfium.PdfDocument(file_bytes)
    try:
        total_pages = len(pdf)
        if total_pages == 0:
            raise ValueError("PDF contains 0 pages.")
        if total_pages > max_pages:
            raise ValueError(
                f"PDF contains {total_pages} pages, exceeding the maximum limit of {max_pages} pages (PDF_MAX_PAGES)."
            )

        scale = dpi / 72.0
        pages: list[RasterizedPage] = []

        for idx in range(total_pages):
            page = pdf[idx]
            bitmap = page.render(scale=scale)
            pil_img = bitmap.to_pil().convert("RGB")
            width, height = pil_img.size

            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            png_bytes = buf.getvalue()
            b64_str = base64.b64encode(png_bytes).decode("ascii")

            pages.append(
                RasterizedPage(
                    page_number=idx + 1,
                    width=width,
                    height=height,
                    png_bytes=png_bytes,
                    image_data_uri=f"data:image/png;base64,{b64_str}",
                )
            )
            page.close()

        return mime_type, pages
    finally:
        pdf.close()
