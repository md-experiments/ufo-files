"""Text extraction from PDFs, with per-page OCR fallback for scanned pages.

Each page is read with PyMuPDF first. Pages that have (almost) no embedded text
— typical for scanned Project Blue Book files, FBI vault scans and redacted
mission reports — are rendered to an image and run through Tesseract.
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
from concurrent.futures import as_completed
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .config import get_settings

log = logging.getLogger(__name__)

# Tesseract's OpenMP threads fight each other when several pages are OCR'd in
# parallel (50x slowdown measured on 4 cores); parallelise across pages instead.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")


@dataclass
class PageText:
    page_no: int
    text: str
    method: str  # text | ocr | empty
    confidence: float | None = None


@dataclass
class Extraction:
    pages: list[PageText] = field(default_factory=list)
    page_count: int = 0
    truncated: bool = False

    @property
    def text(self) -> str:
        return "\n\n".join(f"[Page {p.page_no}]\n{p.text}" for p in self.pages if p.text.strip())

    @property
    def ocr_pages(self) -> list[PageText]:
        return [p for p in self.pages if p.method == "ocr"]

    @property
    def ocr_confidence(self) -> float | None:
        confs = [p.confidence for p in self.ocr_pages if p.confidence is not None]
        return round(sum(confs) / len(confs), 1) if confs else None


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


_WS = re.compile(r"[ \t]+")


def clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r", "")
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def meaningful_chars(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


_WORD = re.compile(r"[A-Za-z]{2,}")
_TOKEN = re.compile(r"\S+")


def text_quality(text: str) -> float:
    """Share of whitespace-separated tokens that look like plain words (0..1)."""
    tokens = _TOKEN.findall(text)
    if not tokens:
        return 0.0
    good = sum(1 for t in tokens if _WORD.fullmatch(t.strip(".,;:()[]\"'!?-")))
    return good / len(tokens)


def needs_ocr(text: str, page: "pymupdf.Page", min_chars: int) -> bool:
    if meaningful_chars(text) < min_chars:
        return True
    # Pages dominated by an image with only a thin text layer (e.g. a stamp or
    # a Bates number over a scan) still need OCR.
    try:
        area = abs(page.rect)
        img_area = sum(abs(r) for info in page.get_images(full=True) for r in page.get_image_rects(info[0]))
        if area and img_area / area > 0.6 and meaningful_chars(text) < 400:
            return True
    except Exception:  # pragma: no cover - defensive, image introspection is best-effort
        pass
    return False


def ocr_image(image, lang: str, timeout: int = 0) -> tuple[str, float | None]:
    """OCR a PIL image (or PNG bytes). Returns (text, mean word confidence)."""
    import pytesseract
    from PIL import Image

    img = Image.open(io.BytesIO(image)) if isinstance(image, (bytes, bytearray)) else image
    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT, timeout=timeout)
    lines: dict[tuple[int, int, int], list[str]] = {}
    confs: list[float] = []
    for i, word in enumerate(data["text"]):
        word = (word or "").strip()
        conf = float(data["conf"][i])
        if not word or conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
        confs.append(conf)
    text_lines = []
    prev_block = None
    for key in sorted(lines):
        if prev_block is not None and key[0] != prev_block:
            text_lines.append("")
        text_lines.append(" ".join(lines[key]))
        prev_block = key[0]
    conf = round(sum(confs) / len(confs), 1) if confs else None
    return "\n".join(text_lines), conf


def _score(text: str) -> float:
    return meaningful_chars(text) * (0.25 + text_quality(text))


def _to_image(pix: "pymupdf.Pixmap"):
    from PIL import Image

    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def is_blank(page: "pymupdf.Page", threshold: float = 0.002) -> bool:
    """Cheap low-resolution check for (nearly) empty scanned pages."""
    hist = _to_image(page.get_pixmap(dpi=40, colorspace=pymupdf.csGRAY)).histogram()
    total = sum(hist) or 1
    return sum(hist[:128]) / total < threshold


# --- OCR worker processes ----------------------------------------------------
# Rendering (PyMuPDF) and recognition (Tesseract) both run in worker processes
# that open the PDF themselves, so neither serialises on the main thread.

_worker_docs: dict[str, "pymupdf.Document"] = {}


def _ocr_page_task(path: str, index: int, dpi: int, lang: str, timeout: int) -> tuple[int, str, float | None, str | None]:
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    try:
        doc = _worker_docs.get(path)
        if doc is None:
            for old in list(_worker_docs.values()):
                old.close()
            _worker_docs.clear()
            doc = _worker_docs[path] = pymupdf.open(path)
        page = doc.load_page(index)
        if is_blank(page):
            return index, "", None, None
        img = _to_image(page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY))
        text, conf = ocr_image(img, lang, timeout)
        return index, text, conf, None
    except Exception as exc:  # reported back, never kills the pool
        return index, "", None, f"{type(exc).__name__}: {exc}"


_pool = None
_pool_size = 0


def _get_pool(workers: int):
    global _pool, _pool_size
    if _pool is None or _pool_size != workers:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor

        if _pool is not None:
            _pool.shutdown(cancel_futures=True)
        _pool = ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn"))
        _pool_size = workers
    return _pool


def extract_pdf(path: Path | str, ocr: bool | None = None, force_ocr: bool | None = None) -> Extraction:
    """Extract text page by page.

    ``force_ocr`` (default: ``OCR_MODE=always``) OCRs every page and keeps the
    OCR result wherever it reads better than the embedded text layer; many
    released files carry a poor-quality government OCR layer.
    """
    s = get_settings()
    ocr = s.ocr_enabled if ocr is None else ocr
    force_ocr = (s.ocr_mode == "always") if force_ocr is None else force_ocr
    if ocr and not tesseract_available():
        log.warning("tesseract not installed; scanned pages will be left empty")
        ocr = False

    path = str(Path(path).resolve())
    doc = pymupdf.open(path)
    result = Extraction(page_count=doc.page_count)
    n = min(doc.page_count, s.max_pages)
    result.truncated = doc.page_count > n

    to_ocr: list[int] = []
    for i in range(n):
        page = doc.load_page(i)
        text = clean_text(page.get_text("text", sort=True))
        result.pages.append(PageText(i + 1, text, "text" if text else "empty"))
        if ocr and (force_ocr or needs_ocr(text, page, s.ocr_min_chars)):
            to_ocr.append(i)
    doc.close()

    if to_ocr:
        pool = _get_pool(max(1, s.ocr_workers))
        futures = [pool.submit(_ocr_page_task, path, i, s.ocr_dpi, s.ocr_lang, s.ocr_page_timeout) for i in to_ocr]
        for fut in as_completed(futures):
            i, text, conf, err = fut.result()
            if err:  # keep going; one bad page shouldn't sink the doc
                log.warning("OCR failed on %s page %d: %s", path, i + 1, err)
                continue
            text = clean_text(text)
            # keep whichever is better: OCR or the embedded layer (ties go to
            # the embedded text, which is exact when present)
            if _score(text) > _score(result.pages[i].text) * 1.05:
                result.pages[i] = PageText(i + 1, text, "ocr" if text else "empty", conf)
    return result
