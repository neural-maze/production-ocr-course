import re

from ocr_pipeline.engine.base import BoundingBox, ParsedPage

# Unlimited-OCR-2 block grammar:
# <BLOCK>label[x0, y0, x1, y1]<FORK>content
BLOCK_PATTERN = re.compile(
    r"<BLOCK>\s*([a-zA-Z0-9_/-]*)\s*\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]\s*<FORK>(.*?)(?=<BLOCK>|<PAGE>|$)",
    re.DOTALL,
)

# Unlimited-OCR v1 grounding grammar fallback:
# <|det|>label[[x0, y0, x1, y1]]<|/det|>
DET_V1_PATTERN = re.compile(r"<\|det\|>([\s\S]*?)<\|/det\|>")
REF_V1_PATTERN = re.compile(r"<\|ref\|>([\s\S]*?)<\|/ref\|>")
NUM_PATTERN = re.compile(r"\d+")

# Special end-of-sequence or control tokens to strip
SPECIAL_TOKENS = (
    "<|eos|>",
    "<|end▁of▁sentence|>",
    "<｜end▁of▁sentence｜>",
    "<|im_end|>",
    "</s>",
)


def _clamp_coord(val: int) -> int:
    return max(0, min(1000, val))


def _strip_special_tokens(text: str) -> str:
    cleaned = text
    for tok in SPECIAL_TOKENS:
        cleaned = cleaned.replace(tok, "")
    return cleaned.strip()


def _format_block_as_markdown(label: str, text: str) -> str:
    """Formats a single Unlimited-OCR-2 block into clean Markdown based on its semantic label."""
    content = _strip_special_tokens(text)
    if not content:
        return ""

    norm_label = label.lower().strip()
    base_label = norm_label.removeprefix("cross_").removeprefix("append_")

    if base_label in ("header", "page_header", "footer", "page_footer", "page_number"):
        # Keep headers/footers subtle or omit noise if empty
        return content

    if base_label in ("title", "doc_title"):
        if not content.startswith("#"):
            return f"# {content}"
        return content

    if base_label in ("section_header", "sub_title", "subtitle", "heading"):
        if not content.startswith("#"):
            return f"## {content}"
        return content

    if base_label in ("formula", "equation", "display_formula"):
        if not (content.startswith("$$") or content.startswith("\\[")):
            return f"$$\n{content}\n$$"
        return content

    return content


def _parse_single_page_v2(raw_page: str, page_offset: int) -> ParsedPage:
    """Parses a single <PAGE> segment containing <BLOCK>...<FORK> markers."""
    bboxes: list[BoundingBox] = []
    md_blocks: list[str] = []

    matches = list(BLOCK_PATTERN.finditer(raw_page))
    if not matches:
        # Check if v1 <|det|> tags exist
        if "<|det|>" in raw_page:
            return _parse_single_page_v1(raw_page, page_offset)

        # Plain markdown fallback
        clean = _strip_special_tokens(raw_page.replace("<PAGE>", "").replace("<FORK>", ""))
        return ParsedPage(
            page_offset=page_offset,
            markdown=clean,
            raw_text=raw_page.strip(),
            bboxes=[],
        )

    for match in matches:
        raw_label = match.group(1).strip() or "text"
        x0 = _clamp_coord(int(match.group(2)))
        y0 = _clamp_coord(int(match.group(3)))
        x1 = _clamp_coord(int(match.group(4)))
        y1 = _clamp_coord(int(match.group(5)))
        raw_content = _strip_special_tokens(match.group(6))

        xmin, xmax = min(x0, x1), max(x0, x1)
        ymin, ymax = min(y0, y1), max(y0, y1)

        bboxes.append(
            BoundingBox(
                label=raw_label.lower(),
                xmin=xmin,
                ymin=ymin,
                xmax=xmax,
                ymax=ymax,
                text=raw_content,
            )
        )

        formatted = _format_block_as_markdown(raw_label, raw_content)
        if formatted:
            md_blocks.append(formatted)

    return ParsedPage(
        page_offset=page_offset,
        markdown="\n\n".join(md_blocks).strip(),
        raw_text=raw_page.strip(),
        bboxes=bboxes,
    )


def _parse_single_page_v1(raw_page: str, page_offset: int) -> ParsedPage:
    """Fallback parser for Unlimited-OCR v1 (<|det|> and <|ref|> tags)."""
    bboxes: list[BoundingBox] = []
    matches = list(DET_V1_PATTERN.finditer(raw_page))

    for i, match in enumerate(matches):
        inside = match.group(1)
        nums = [int(n) for n in NUM_PATTERN.findall(inside)]
        if len(nums) >= 4:
            x0, y0, x1, y1 = (_clamp_coord(nums[0]), _clamp_coord(nums[1]), _clamp_coord(nums[2]), _clamp_coord(nums[3]))
            label_part = inside.split("[", 1)[0].strip().lower() or "text"

            text_start = match.end()
            text_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_page)
            chunk = raw_page[text_start:text_end]
            clean_chunk = _strip_special_tokens(chunk.replace("<|ref|>", "").replace("<|/ref|>", ""))

            bboxes.append(
                BoundingBox(
                    label=label_part,
                    xmin=min(x0, x1),
                    ymin=min(y0, y1),
                    xmax=max(x0, x1),
                    ymax=max(y0, y1),
                    text=clean_chunk,
                )
            )

    stripped_det = DET_V1_PATTERN.sub("", raw_page)
    unwrapped_ref = REF_V1_PATTERN.sub(r"\1", stripped_det)
    clean_md = _strip_special_tokens(unwrapped_ref)

    return ParsedPage(
        page_offset=page_offset,
        markdown=clean_md,
        raw_text=raw_page.strip(),
        bboxes=bboxes,
    )


def parse_multi_page_output(raw_output: str, expected_pages: int) -> list[ParsedPage]:
    """
    Splits a multi-page Unlimited-OCR-2 response by `<PAGE>` markers and parses
    each page into clean Markdown and normalized bounding boxes.
    """
    cleaned_raw = _strip_special_tokens(raw_output)

    if "<PAGE>" in cleaned_raw:
        raw_segments = [seg.strip() for seg in cleaned_raw.split("<PAGE>") if seg.strip()]
    else:
        raw_segments = [cleaned_raw] if cleaned_raw else []

    parsed_pages: list[ParsedPage] = []
    for idx, segment in enumerate(raw_segments):
        parsed_pages.append(_parse_single_page_v2(segment, page_offset=idx))

    # Ensure we return one ParsedPage per input image in the batch
    if len(parsed_pages) < expected_pages:
        if len(parsed_pages) == 1 and expected_pages > 1:
            # Model returned a single combined output without <PAGE> splits; attach to first page
            for extra_idx in range(1, expected_pages):
                parsed_pages.append(
                    ParsedPage(
                        page_offset=extra_idx,
                        markdown="",
                        raw_text="",
                        bboxes=[],
                    )
                )
        else:
            for extra_idx in range(len(parsed_pages), expected_pages):
                parsed_pages.append(
                    ParsedPage(
                        page_offset=extra_idx,
                        markdown="",
                        raw_text="",
                        bboxes=[],
                    )
                )
    elif len(parsed_pages) > expected_pages and expected_pages > 0:
        # Merge any trailing overflow segments into the last expected page
        head = parsed_pages[: expected_pages - 1]
        tail = parsed_pages[expected_pages - 1 :]
        merged_md = "\n\n".join(p.markdown for p in tail if p.markdown)
        merged_raw = "\n<PAGE>\n".join(p.raw_text for p in tail if p.raw_text)
        merged_bboxes: list[BoundingBox] = []
        for p in tail:
            merged_bboxes.extend(p.bboxes)
        head.append(
            ParsedPage(
                page_offset=expected_pages - 1,
                markdown=merged_md,
                raw_text=merged_raw,
                bboxes=merged_bboxes,
            )
        )
        parsed_pages = head

    return parsed_pages
