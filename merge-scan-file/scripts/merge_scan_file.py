#!/usr/bin/env python3
"""Merge two duplex-scanned PDF files into a single ordered PDF.

Priority rules:
1) Prefer footer marker detection like x/n (e.g. 3/12), then sort by x.
2) If marker detection is not reliable, fallback to filename sequence:
   smaller sequence file is treated as odd pages, larger as even pages.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pypdf import PdfReader, PdfWriter

MARKER_SLASH_RE = re.compile(r"(?<!\d)(\d{1,4})\s*/\s*(\d{1,4})(?!\d)", re.IGNORECASE)
MARKER_OF_RE = re.compile(r"(?<!\d)(\d{1,4})\s*of\s*(\d{1,4})(?!\d)", re.IGNORECASE)
MARKER_PAGE_OF_RE = re.compile(r"\bpage\s+(\d{1,4})\s+of\s+(\d{1,4})\b", re.IGNORECASE)
MARKER_PG_OF_RE = re.compile(r"\bpg\.?\s+(\d{1,4})\s+of\s+(\d{1,4})\b", re.IGNORECASE)
PURE_X_LINE_RE = re.compile(r"^\s*(\d{1,4})\s*$")
FILE_SEQ_RE = re.compile(r"(\d+)")
OUT_RE = re.compile(r"^Merged_(\d{3})\.pdf$", re.IGNORECASE)
BLANK_DARK_RATIO_THRESHOLD = 0.006


@dataclass
class PageRef:
    file_idx: int
    page_idx: int
    marker_x: Optional[int]
    marker_n: Optional[int]


class OcrHelper:
    def __init__(self) -> None:
        self.visual_available = False
        self.ocr_available = False
        self._fitz = None
        self._pytesseract = None
        self._image = None
        self._docs: dict[str, object] = {}
        try:
            import fitz  # type: ignore
            self._fitz = fitz
            self.visual_available = True
        except Exception:
            self.visual_available = False

        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore

            self._pytesseract = pytesseract
            self._image = Image
            self.ocr_available = True
        except Exception:
            self.ocr_available = False

    def marker_from_page(self, pdf_path: Path, page_idx: int) -> tuple[Optional[int], Optional[int]]:
        if not (self.visual_available and self.ocr_available):
            return (None, None)
        try:
            key = str(pdf_path.resolve())
            if key not in self._docs:
                self._docs[key] = self._fitz.open(key)
            doc = self._docs[key]
            page = doc[page_idx]
            rect = page.rect
            clip = self._fitz.Rect(
                rect.x0,
                rect.y0 + rect.height * 0.74,
                rect.x1,
                rect.y1,
            )
            pix = page.get_pixmap(matrix=self._fitz.Matrix(2, 2), clip=clip)
            mode = "RGBA" if pix.alpha else "RGB"
            img = self._image.frombytes(mode, [pix.width, pix.height], pix.samples)
            text = self._pytesseract.image_to_string(img, lang="eng")
            return parse_marker(text)
        except Exception:
            return (None, None)

    def visually_blank_page(self, pdf_path: Path, page_idx: int) -> Optional[bool]:
        """Best-effort visual blank-page detection.

        Returns:
        - True/False when fitz is available and detection succeeds.
        - None when detection is unavailable.
        """
        if not self.visual_available:
            return None
        try:
            key = str(pdf_path.resolve())
            if key not in self._docs:
                self._docs[key] = self._fitz.open(key)
            doc = self._docs[key]
            page = doc[page_idx]
            pix = page.get_pixmap(matrix=self._fitz.Matrix(0.6, 0.6), colorspace=self._fitz.csGRAY)
            samples = pix.samples
            if not samples:
                return True
            dark = 0
            total = len(samples)
            for b in samples:
                if b < 245:
                    dark += 1
            dark_ratio = dark / total
            return dark_ratio < BLANK_DARK_RATIO_THRESHOLD
        except Exception:
            return None

    def close(self) -> None:
        for doc in self._docs.values():
            try:
                doc.close()
            except Exception:
                pass
        self._docs.clear()


def parse_marker(text: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    if not text:
        return (None, None)

    explicit_patterns = [MARKER_PAGE_OF_RE, MARKER_PG_OF_RE, MARKER_SLASH_RE, MARKER_OF_RE]
    for pattern in explicit_patterns:
        matches = list(pattern.finditer(text))
        for m in reversed(matches):
            x = int(m.group(1))
            n = int(m.group(2))
            if 1 <= x <= n:
                return (x, n)

    for line in reversed([ln.strip() for ln in text.splitlines() if ln.strip()]):
        m = PURE_X_LINE_RE.match(line)
        if m:
            x = int(m.group(1))
            if x >= 1:
                return (x, None)
    return (None, None)


def extract_file_seq(pdf_path: Path) -> Optional[int]:
    matches = FILE_SEQ_RE.findall(pdf_path.stem)
    if not matches:
        return None
    return int(matches[-1])


def next_output_path(output_dir: Path) -> Path:
    max_num = 0
    for p in output_dir.glob("Merged_*.pdf"):
        m = OUT_RE.match(p.name)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return output_dir / f"Merged_{max_num + 1:03d}.pdf"


def collect_page_refs(pdf_paths: list[Path], readers: list[PdfReader]) -> list[PageRef]:
    refs: list[PageRef] = []
    ocr = OcrHelper()
    try:
        for file_idx, (pdf_path, reader) in enumerate(zip(pdf_paths, readers)):
            for page_idx, page in enumerate(reader.pages):
                if is_blank_page(pdf_path, page_idx, page, ocr):
                    continue
                marker_x, marker_n = parse_marker(page.extract_text())
                if marker_x is None:
                    marker_x, marker_n = ocr.marker_from_page(pdf_path, page_idx)
                refs.append(
                    PageRef(
                        file_idx=file_idx,
                        page_idx=page_idx,
                        marker_x=marker_x,
                        marker_n=marker_n,
                    )
                )
    finally:
        ocr.close()
    return refs


def is_blank_page(pdf_path: Path, page_idx: int, page, ocr: OcrHelper) -> bool:
    text = page.extract_text() or ""
    visual = ocr.visually_blank_page(pdf_path, page_idx)
    if visual is not None:
        if visual:
            return True
        if text.strip():
            return False

    if text.strip():
        return False

    # Fallback when visual detection is unavailable:
    # no text + no embedded images => blank.
    try:
        has_images = len(list(page.images)) > 0
    except Exception:
        has_images = True
    return not has_images


def marker_strategy_order(refs: list[PageRef]) -> Optional[list[PageRef]]:
    total = len(refs)
    marked = [r for r in refs if r.marker_x is not None]
    if len(marked) != total:
        return None
    x_values = [r.marker_x for r in marked if r.marker_x is not None]
    if len(set(x_values)) != total:
        return None
    return sorted(marked, key=lambda r: (r.marker_x or 10**9, r.file_idx, r.page_idx))


def fallback_interleave_order(
    pdf_paths: list[Path],
    non_blank_by_file: dict[int, list[int]],
) -> list[PageRef]:
    seq0 = extract_file_seq(pdf_paths[0])
    seq1 = extract_file_seq(pdf_paths[1])

    odd_idx = 0
    even_idx = 1
    if seq0 is not None and seq1 is not None and seq0 > seq1:
        odd_idx, even_idx = 1, 0

    odd_pages = non_blank_by_file.get(odd_idx, [])
    even_pages = list(reversed(non_blank_by_file.get(even_idx, [])))

    ordered: list[PageRef] = []
    for i in range(max(len(odd_pages), len(even_pages))):
        if i < len(odd_pages):
            ordered.append(PageRef(file_idx=odd_idx, page_idx=odd_pages[i], marker_x=None, marker_n=None))
        if i < len(even_pages):
            ordered.append(PageRef(file_idx=even_idx, page_idx=even_pages[i], marker_x=None, marker_n=None))
    return ordered


def merge_two_pdfs(pdf_a: Path, pdf_b: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_paths = [pdf_a, pdf_b]
    readers = [PdfReader(str(pdf_a)), PdfReader(str(pdf_b))]
    refs = collect_page_refs(pdf_paths, readers)
    non_blank_by_file: dict[int, list[int]] = {0: [], 1: []}
    for ref in refs:
        non_blank_by_file.setdefault(ref.file_idx, []).append(ref.page_idx)

    ordered_refs = marker_strategy_order(refs)
    mode = "marker"
    if ordered_refs is None:
        ordered_refs = fallback_interleave_order(pdf_paths, non_blank_by_file)
        mode = "fallback-filename-interleave"

    writer = PdfWriter()
    for ref in ordered_refs:
        writer.add_page(readers[ref.file_idx].pages[ref.page_idx])

    out_path = next_output_path(output_dir)
    with out_path.open("wb") as f:
        writer.write(f)

    print(f"merge_mode={mode}")
    print(f"output={out_path}")
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge duplex scanned PDFs into one ordered PDF.")
    parser.add_argument("pdf_a", type=Path, help="First PDF path")
    parser.add_argument("pdf_b", type=Path, help="Second PDF path")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd(),
        help="Directory for merged output PDF (default: current directory)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    for p in [args.pdf_a, args.pdf_b]:
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        if p.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF file: {p}")

    merge_two_pdfs(args.pdf_a, args.pdf_b, args.output_dir)


if __name__ == "__main__":
    main()
