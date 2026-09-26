"""Find the passages two records have in common.

Each record is cut into passages: the publisher's description, the summary and
key points, and every sentence of the extracted (often OCR'd) page text. OCR
debris is dropped. Passages are compared with TF-IDF over content words, and
the best non-overlapping pairs are returned with the words they share.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Document, Page
from .links import STOPWORDS_EXTRA

# archive furniture that appears on every page and says nothing about a sighting
ARCHIVE_WORDS = {
    "approved", "release", "released", "declassified", "classified", "unclassified", "secret", "confidential",
    "authority", "nnd", "copy", "copies", "page", "pages", "subject", "date", "dated", "memorandum", "memo",
    "reference", "ref", "info", "information", "form", "forms", "office", "file", "files", "number", "cia",
    "fbi", "usaf", "army", "navy", "department", "director", "attention", "control", "registry", "received",
    "air", "force", "base", "headquarters", "stated", "advised", "letter", "enclosure", "incl", "sincerely",
    "government", "printing", "top", "officers", "downgraded", "edition", "editions",
}
SHORT_WORDS = {"a", "i", "an", "as", "at", "be", "by", "do", "he", "if", "in", "is", "it", "me", "my", "no", "of",
               "on", "or", "so", "to", "up", "us", "we", "am", "go", "mr", "dr", "st", "ft", "mi", "am", "pm"}
MAX_PASSAGES = 4000  # per record; the biggest files have ~400 pages
MIN_SCORE = 0.2
MIN_SHARED = 3  # shared content words; 2 will do for a strong match

_WS = re.compile(r"\s+")
_LEAD_JUNK = re.compile(r"^(?:[^A-Za-z0-9\"“(]+|[A-Za-z0-9](?=[^A-Za-z0-9]))+")  # OCR debris before a sentence
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[\"“(A-Z0-9])")
_TOKEN = re.compile(r"(?u)\b[a-zA-Z][a-zA-Z-]{2,}\b")
_WORDLIKE = re.compile(r"[\"“(']?(?:[A-Za-z][a-z]+(?:-[a-z]+)*|[A-Z]{2,}|\d{1,4})[\"”),.;:'!?]*")


@dataclass
class Passage:
    text: str
    where: str  # "Description", "Page 3", ...
    page: int | None = None


def _readable(s: str) -> bool:
    """True when a passage is mostly real words, not OCR noise."""
    toks = s.split()
    if len(toks) < 6:
        return False
    good = 0
    for t in toks:
        core = t.strip("\"“”()',.;:!?")
        if not _WORDLIKE.fullmatch(t) or not re.search(r"[aeiouyAEIOUY0-9]", t):
            continue
        if len(core) <= 2 and not core.isdigit() and core.lower() not in SHORT_WORDS:
            continue
        good += 1
    return good / len(toks) >= 0.75


def _chunks(text: str, size: int = 320) -> list[str]:
    """Sentences; overlong runs (OCR without punctuation) are cut at word breaks."""
    out = []
    for s in _SPLIT.split(_WS.sub(" ", text).strip()):
        while len(s) > size + 80:
            cut = s.rfind(" ", 0, size)
            cut = cut if cut > 0 else size
            out.append(s[:cut])
            s = s[cut:].lstrip()
        out.append(s)
    out = [_LEAD_JUNK.sub("", s).strip() for s in out]
    return [s for s in out if len(s) >= 40]


def passages(doc: Document, pages: list[tuple[int, str]]) -> list[Passage]:
    out: list[Passage] = []
    seen: set[str] = set()

    def add(text: str, where: str, page: int | None = None) -> None:
        key = _WS.sub(" ", text.lower()).strip()
        if key in seen or not _readable(text):
            return
        seen.add(key)
        out.append(Passage(text, where, page))

    for s in _chunks(doc.description or ""):
        add(s, "Publisher's description")
    for s in _chunks(doc.summary or ""):
        add(s, "Summary")
    for kp in doc.key_points or []:
        if isinstance(kp, str):
            add(kp, "Key point")
    for page_no, text in pages:
        for s in _chunks(text or ""):
            if len(out) >= MAX_PASSAGES:
                return out
            add(s, f"Page {page_no}", page_no)
    return out


def load_passages(db: Session, doc: Document) -> list[Passage]:
    pages = db.execute(select(Page.page_no, Page.text).where(Page.document_id == doc.id)
                       .order_by(Page.page_no)).all()
    return passages(doc, [(p, t) for p, t in pages])


def match(pa: list[Passage], pb: list[Passage], limit: int = 12, boiler: set[str] | None = None) -> list[dict]:
    """The best-matching passage pairs, strongest first, each passage used once."""
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

    from . import _norm_sentence

    if boiler:
        pa = [p for p in pa if _norm_sentence(p.text) not in boiler]
        pb = [p for p in pb if _norm_sentence(p.text) not in boiler]
    if not pa or not pb:
        return []
    stop = ENGLISH_STOP_WORDS | STOPWORDS_EXTRA | ARCHIVE_WORDS
    tf = TfidfVectorizer(stop_words=list(stop), ngram_range=(1, 2), sublinear_tf=True, lowercase=True,
                         token_pattern=_TOKEN.pattern)
    try:
        x = tf.fit_transform([p.text for p in pa] + [p.text for p in pb])
    except ValueError:  # nothing but stop words
        return []
    xa, xb = x[: len(pa)], x[len(pa):]
    sims = (xa @ xb.T).tocoo()
    cand = sorted(((float(v), int(i), int(j)) for v, i, j in zip(sims.data, sims.row, sims.col) if v >= MIN_SCORE),
                  reverse=True)
    vocab = tf.get_feature_names_out()
    used_a: set[int] = set()
    used_b: set[int] = set()
    used_text: set[tuple[str, str]] = set()
    out = []
    for score, i, j in cand:
        if i in used_a or j in used_b:
            continue
        shared = set(xa[i].indices) & set(xb[j].indices)
        words = sorted({w for k in shared for w in vocab[k].split()})
        unigrams = [vocab[k] for k in shared if " " not in vocab[k]]
        if len(unigrams) < (2 if score >= 0.3 else MIN_SHARED):
            continue
        key = (pa[i].text.lower(), pb[j].text.lower())
        if key in used_text:
            continue
        used_a.add(i)
        used_b.add(j)
        used_text.add(key)
        # rank richer matches above one-line form labels that happen to be identical
        strength = score * min(1.0, (len(unigrams) + 1) / 6)
        out.append({"score": round(score, 3), "strength": round(strength, 3), "terms": words,
                    "a": {"text": pa[i].text, "where": pa[i].where, "page": pa[i].page},
                    "b": {"text": pb[j].text, "where": pb[j].where, "page": pb[j].page}})
        if len(out) >= limit * 2:
            break
    out.sort(key=lambda m: -m["strength"])
    return out[:limit]


def link_previews(db: Session, links: list[dict], boiler: set[str]) -> None:
    """Attach the best matching passage pair and a match count to each link."""
    ids = {x for link in links for x in (link["a"], link["b"])}
    if not ids:
        return
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.id.in_(ids)))}
    pages: dict[int, list] = defaultdict(list)
    for doc_id, page_no, text in db.execute(select(Page.document_id, Page.page_no, Page.text)
                                            .where(Page.document_id.in_(ids)).order_by(Page.page_no)):
        pages[doc_id].append((page_no, text))
    cache = {i: passages(docs[i], pages[i]) for i in ids if i in docs}
    for link in links:
        found = match(cache.get(link["a"], []), cache.get(link["b"], []), limit=12, boiler=boiler)
        link["matches"] = len(found)
        link["best"] = found[0] if found else None
