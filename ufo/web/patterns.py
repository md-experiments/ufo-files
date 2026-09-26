"""Queries and view-models for the Patterns page and related views."""
from __future__ import annotations

from collections import Counter, defaultdict

from markupsafe import Markup, escape
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analyze import load_results
from ..analyze.features import BY_KEY, GROUPS
from ..analyze.places import place_label
from ..classify.taxonomy import FACETS, label
from ..db import Account, Document, Mention, Observation, Release, Tag
from . import charts

MEDIA_CLS = {"pdf": "s-pdf", "video": "s-video", "image": "s-image", "audio": "s-audio"}


def doc_titles(db: Session, ids) -> dict[int, dict]:
    ids = list({int(i) for i in ids})
    out = {}
    for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
        for d in db.execute(select(Document.id, Document.record_id, Document.title, Document.incident_year,
                                   Document.agency, Document.media_type).where(Document.id.in_(chunk))):
            out[d.id] = {"id": d.id, "record_id": d.record_id, "title": d.title, "year": d.incident_year,
                         "agency": d.agency, "media": d.media_type}
    return out


def tag_label(tag: str) -> str:
    facet, _, value = tag.partition(":")
    if facet == "observable":
        return BY_KEY[value].label if value in BY_KEY else value
    return label(facet, value)


def patterns_context(db: Session) -> dict:
    r = load_results(db)
    if not r:
        return {"ready": False}
    tl = r["timeline"]
    fd = r["feature_decades"]
    features = sorted(r["features"], key=lambda f: -f["records"])
    grouped = defaultdict(list)
    for f in r["features"]:
        if f["pages"]:
            grouped[f["group"]].append(f)
    groups = [(g, GROUPS[g], sorted(grouped[g], key=lambda f: -f["pages"])) for g in GROUPS if grouped.get(g)]

    heat = charts.heatmap(
        [(row["key"], row["label"]) for row in fd["rows"]],
        fd["decades"],
        [[c["share"] for c in row["cells"]] for row in fd["rows"]],
        [[f'{row["label"]}, {c["decade"]}: {c["count"]} of {tot} sighting pages ({100 * c["share"]:.1f}%)'
          for c, tot in zip(row["cells"], fd["totals"])] for row in fd["rows"]],
        per_row=True, row_link="/patterns/evidence?feature={key}",
    )

    types = [c for c in r.get("clusters", []) if "signature" in c]  # older results have no signatures
    ex_ids = {a for t in types for a in t["accounts"][:40]}
    accs = _accounts(db, ex_ids)
    titles = doc_titles(db, {a.document_id for a in accs.values()} | {p["doc"] for p in r["map"] if "doc" in p})
    for t in types:
        t["name"] = type_name(t)
        t["chips"] = ev_chips(t["signature"])
        t["also_labels"] = [(ev_label(k), share) for k, share in t.get("also", [])[:5]]
        t["examples"] = _examples(t, accs, titles, k=2)
    links = _link_views(db, [l for l in r["links"] if "acc_a" in l])
    points = [p for p in r["map"] if "doc" in p]
    for p in points:
        p["href"] = f'/documents/{p["doc"]}' + (f'#p{p["page"]}' if p["page"] else "")
    map_titles = {p["id"]: f'{(titles.get(p["doc"]) or {}).get("title", "")} · '
                           f'{"page " + str(p["page"]) if p["page"] else "description"} — '
                           + ", ".join(ev_label(k) for k in p.get("tags", [])) for p in points}
    cooc = r["cooccurrence"]
    top_pairs = sorted(cooc, key=lambda p: (-p["lift"]))[:14]
    max_lift = max((p["lift"] for p in top_pairs), default=1)

    for w in tl["waves"]:
        w["months_svg"] = charts.month_bars(w["month_counts"], w["peak_month"], f'{w["start"]}')
    rare = sorted((f for f in r["features"] if 0 < f["records"] <= 12), key=lambda f: f["records"])

    return {
        "ready": True,
        "computed_at": r.get("computed_at"),
        "overview": r["overview"],
        "timeline_svg": charts.responsive(charts.timeline(tl["years"], tl["waves"]),
                                          charts.timeline(tl["years"], tl["waves"], narrow=True)),
        "waves": tl["waves"],
        "features": features,
        "groups": groups,
        "decades": fd["decades"],
        "heatmap": heat,
        "sparks": {f["key"]: charts.decade_spark(f["decades"], fd["decades"]) for f in r["features"]},
        "pairs": top_pairs,
        "max_lift": max_lift,
        "tilemap": charts.us_tilemap(r["places"]["us_grid"]),
        "world": r["places"]["world"],
        "top_places": r["places"]["top"],
        "place_decades": r["places"]["decades"],
        "clusters": types,
        "accounts": r.get("accounts", 0),
        "case_map": charts.responsive(
            charts.case_map(points, types, map_titles),
            charts.case_map(points, types, map_titles, narrow=True)),
        "links": links,
        "rare": rare,
    }


def evidence(db: Session, feature: str | None, year: int | None, place: str | None, limit: int = 300) -> dict:
    """Sighting pages matching a detail, a year mentioned, and/or a place."""
    units = None

    def restrict(q):
        nonlocal units
        found = {(d, p) for d, p in db.execute(q)}
        units = found if units is None else units & found

    if feature:
        restrict(select(Observation.document_id, Observation.page_no).where(Observation.feature == feature))
    if year:
        restrict(select(Mention.document_id, Mention.page_no).where(
            Mention.kind == "date", Mention.value.like(f"{year}-%")))
    if place:
        restrict(select(Mention.document_id, Mention.page_no).where(Mention.kind == "place", Mention.value == place))
    units = units or set()
    total = len(units)
    chosen = sorted(units)[:limit]
    doc_ids = {d for d, _ in chosen}
    titles = doc_titles(db, doc_ids)
    obs = defaultdict(list)
    if doc_ids:
        for d, p, f, snip in db.execute(select(Observation.document_id, Observation.page_no, Observation.feature,
                                               Observation.snippet).where(Observation.document_id.in_(doc_ids))):
            obs[(d, p)].append((f, snip))
    rows = []
    for d, p in chosen:
        items = obs.get((d, p), [])
        focus = [s for f, s in items if f == feature] if feature else []
        rows.append({"doc": titles.get(d), "page": p, "snippet": (focus or [s for _, s in items] or [""])[0],
                     "features": [(f, BY_KEY[f].label) for f, _ in items if f in BY_KEY]})
    # group by record so long files don't flood the list
    by_doc = defaultdict(list)
    for row in rows:
        by_doc[row["doc"]["id"] if row["doc"] else 0].append(row)
    records = sorted(by_doc.values(), key=lambda rs: -len(rs))
    return {"total": total, "records": records, "shown": len(rows),
            "feature": BY_KEY.get(feature) if feature else None, "year": year,
            "place": place, "place_label": place_label(place) if place else None}


def document_patterns(db: Session, doc: Document) -> dict:
    """Details found in one record, and the most similar records elsewhere."""
    obs = db.execute(select(Observation.page_no, Observation.feature, Observation.snippet)
                     .where(Observation.document_id == doc.id).order_by(Observation.page_no)).all()
    per_feature: dict[str, list] = defaultdict(list)
    for p, f, snip in obs:
        per_feature[f].append((p, snip))
    details = [{"key": k, "label": BY_KEY[k].label, "group": GROUPS[BY_KEY[k].group], "pages": v}
               for k, v in sorted(per_feature.items(), key=lambda kv: -len(kv[1])) if k in BY_KEY]
    r = load_results(db)
    sim = [x for x in (r.get("similar") or {}).get(str(doc.id), []) if len(x) >= 5]
    titles = doc_titles(db, [x[0] for x in sim])
    similar = [{"doc": titles[x[0]], "score": x[1], "shared": [ev_label(t) for t in x[2]]} for x in sim if x[0] in titles]
    own = db.scalars(select(Account).where(Account.document_id == doc.id).order_by(Account.page_no, Account.seq)).all()
    own_ids = {a.id for a in own}
    types = [{"id": t["id"], "name": type_name(t), "n": sum(1 for a in t["accounts"] if a in own_ids)}
             for t in r.get("clusters", []) if "signature" in t and own_ids & set(t["accounts"])]
    accounts = [{"page": a.page_no, "chips": ev_chips(a.tags), "excerpt": event_excerpt(a.text, a.spans, width=700)}
                for a in own[:40]]
    return {"details": details, "similar": similar, "types": types, "accounts": accounts, "n_accounts": len(own)}


def releases_visuals(db: Session) -> dict:
    """Charts summarising what each release contained."""
    rels = db.scalars(select(Release).order_by(Release.release_date)).all()
    docs = db.execute(select(Document.id, Document.title, Document.release_id, Document.incident_year,
                             Document.agency, Document.media_type)).all()
    by_rel = defaultdict(list)
    for d in docs:
        by_rel[d.release_id].append(d)
    strip = [{"label": r.label.split(" · ")[0] + " · " + r.release_date.strftime("%b %-d"),
              "short": f"R{r.number}" if r.number else r.release_date.strftime("%b"),
              "docs": [{"id": d.id, "title": d.title, "year": d.incident_year} for d in by_rel[r.id]]}
             for r in rels]

    agencies = Counter(d.agency or "Other" for d in docs)
    top_agencies = [a for a, _ in agencies.most_common(4)]
    agency_cls = {a: f"s-{i + 1}" for i, a in enumerate(top_agencies)}
    stacks = []
    for r in reversed(rels):
        c = Counter(d.agency or "Other" for d in by_rel[r.id])
        segs = [{"label": a, "n": c.get(a, 0), "cls": agency_cls[a]} for a in top_agencies]
        other = sum(n for a, n in c.items() if a not in agency_cls)
        segs.append({"label": "Other agencies", "n": other, "cls": "s-other"})
        stacks.append({"release": r, "total": len(by_rel[r.id]), "bar": charts.stacked_bar(segs, max(1, len(by_rel[r.id])))})

    topics = list(FACETS["topic"])
    counts = defaultdict(Counter)
    for rid, v in db.execute(select(Document.release_id, Tag.value).join(Tag, Tag.document_id == Document.id)
                             .where(Tag.facet == "topic")):
        counts[rid][v] += 1
    used = [t for t in topics if any(counts[r.id][t] for r in rels)]
    heat = charts.heatmap(
        [(t, label("topic", t)) for t in used],
        [r.label.split(" · ")[0].replace("Release ", "R") for r in rels],
        [[counts[r.id][t] / max(1, len(by_rel[r.id])) for r in rels] for t in used],
        [[f'{label("topic", t)} in {r.label}: {counts[r.id][t]} of {len(by_rel[r.id])} records' for r in rels] for t in used],
        per_row=False, row_link="/documents?tag=topic:{key}",
    )
    legend = [(a, agency_cls[a]) for a in top_agencies] + [("Other agencies", "s-other")]
    return {"strip": charts.responsive(charts.strip_plot(strip), charts.strip_plot(strip, narrow=True)), "stacks": stacks, "agency_legend": legend, "topic_heat": heat}


# ---------------------------------------------------------------------------
# Sighting accounts: excerpts, types, comparisons
# ---------------------------------------------------------------------------

def event_excerpt(text: str, spans: list, focus: set[str] | None = None, width: int = 900) -> Markup:
    """The parts of an account around its tagged details, with each detail
    marked; ``focus`` details (e.g. the ones two accounts share) stand out.
    Details far apart get separate windows joined by an ellipsis."""
    text = text or ""
    focus = focus or set()
    spans = sorted(([t, s, e] for t, s, e in spans if 0 <= s < e <= len(text)), key=lambda x: (x[1], -x[2]))
    key_spans = [x for x in spans if x[0] in focus] or spans
    ctx = max(60, min(200, width // 5))
    windows: list[list[int]] = []
    for _, a, b in sorted(key_spans, key=lambda x: x[1]):
        lo, hi = max(0, a - ctx), min(len(text), b + ctx)
        if windows and lo <= windows[-1][1] + 40:
            windows[-1][1] = max(windows[-1][1], hi)
        else:
            windows.append([lo, hi])
    if not windows:
        windows = [[0, min(len(text), width)]]
    for w in windows:  # widen to word boundaries
        while w[0] > 0 and text[w[0] - 1].isalnum():
            w[0] -= 1
        while w[1] < len(text) and text[w[1]].isalnum():
            w[1] += 1
    out = []
    for n, (lo, hi) in enumerate(windows):
        out.append("…" if lo > 0 else "")
        pos = lo
        for t, a, b in spans:
            if a < pos or b > hi:
                continue  # overlaps a detail already marked, or outside this window
            over = [x for x in spans if x[1] < b and a < x[2]]
            labels = " · ".join(ev_label(x[0]) for x in over)
            cls = "ev hit" if any(x[0] in focus for x in over) else "ev"
            out += [str(escape(text[pos:a])), f'<mark class="{cls}" title="{escape(labels)}">', str(escape(text[a:b])), "</mark>"]
            pos = b
        out.append(str(escape(text[pos:hi])))
        out.append("…" if hi < len(text) else "")
        if n < len(windows) - 1:
            out.append(" ")
    return Markup("".join(out))


def ev_label(key: str) -> str:
    from ..analyze.events import tag_label as _l

    return _l(key)


def ev_chips(keys) -> list[dict]:
    """Tags as chips, in dimension order, with their dimension's label."""
    from ..analyze.events import BY_TAG, DIMENSIONS

    order = list(DIMENSIONS)
    ks = sorted((k for k in keys if k in BY_TAG), key=lambda k: order.index(BY_TAG[k].dim))
    return [{"key": k, "label": BY_TAG[k].label, "dim": BY_TAG[k].dim, "dim_label": DIMENSIONS[BY_TAG[k].dim][0],
             "evidence": k in BY_KEY} for k in ks]


def type_name(t: dict) -> str:
    return " · ".join(ev_label(k) for k in t["signature"])


def _accounts(db: Session, ids) -> dict[int, Account]:
    ids = list({int(i) for i in ids})
    out = {}
    for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
        for a in db.scalars(select(Account).where(Account.id.in_(chunk))):
            out[a.id] = a
    return out


def _examples(t: dict, accs: dict[int, Account], titles: dict[int, dict], k: int = 3) -> list[dict]:
    """A few accounts of a type from different records, different agencies first."""
    seen_docs, seen_agencies, picks = set(), set(), []
    for rnd in (0, 1):
        for aid in t["accounts"]:
            a = accs.get(aid)
            if not a or a.document_id in seen_docs or len(picks) >= k:
                continue
            ag = (titles.get(a.document_id) or {}).get("agency")
            if rnd == 0 and ag in seen_agencies:
                continue
            seen_docs.add(a.document_id)
            seen_agencies.add(ag)
            picks.append({"doc": titles.get(a.document_id), "page": a.page_no,
                          "excerpt": event_excerpt(a.text, a.spans, set(t["signature"]), width=320)})
    return picks


def sighting_type(db: Session, type_id: int) -> dict | None:
    r = load_results(db)
    t = next((c for c in r.get("clusters", []) if c["id"] == type_id), None)
    if not t or "signature" not in t:
        return None
    accs = _accounts(db, t["accounts"])
    titles = doc_titles(db, {a.document_id for a in accs.values()})
    by_doc: dict[int, list] = defaultdict(list)
    for aid in t["accounts"]:
        a = accs.get(aid)
        if a:
            by_doc[a.document_id].append({"page": a.page_no, "chips": ev_chips(set(a.tags) - set(t["signature"])),
                                          "excerpt": event_excerpt(a.text, a.spans, set(t["signature"]))})
    records = sorted(({"doc": titles[d], "accounts": v} for d, v in by_doc.items() if d in titles),
                     key=lambda x: ((x["doc"]["year"] or 9999), x["doc"]["title"]))
    return {"type": t, "name": type_name(t), "signature": ev_chips(t["signature"]),
            "also": [(ev_label(k), share) for k, share in t.get("also", [])], "records": records}


def _mentions(db: Session, doc_id: int, page: int) -> tuple[set[str], set[str]]:
    dates, places = set(), set()
    for kind, value, prec in db.execute(select(Mention.kind, Mention.value, Mention.precision)
                                        .where(Mention.document_id == doc_id, Mention.page_no == page)):
        if kind == "place":
            places.add(value)
        elif prec in ("day", "month"):
            dates.add(value if prec == "day" else value[:7])
    return dates, places


def compare(db: Session, a_id: int, b_id: int) -> dict | None:
    """What two records' sighting accounts have in common."""
    from ..analyze.signatures import MATCH_SCORE, pair_detail, same_report

    a, b = db.get(Document, a_id), db.get(Document, b_id)
    if not a or not b or a.id == b.id:
        return None
    r = load_results(db)
    w = r.get("tag_weights") or {}
    link = next((l for l in r.get("links", []) if {l["a"], l["b"]} == {a.id, b.id}), None)
    acc_a = db.scalars(select(Account).where(Account.document_id == a.id).order_by(Account.page_no, Account.seq)).all()
    acc_b = db.scalars(select(Account).where(Account.document_id == b.id).order_by(Account.page_no, Account.seq)).all()

    cands = []
    for x in acc_a:
        for y in acc_b:
            d = pair_detail(x.tags, y.tags, w)
            if d and d["score"] >= MATCH_SCORE * 0.8:
                cands.append((d["score"], x, y, d))
    cands.sort(key=lambda c: -c[0])
    used_a, used_b, pairs = set(), set(), []
    for score, x, y, d in cands:
        if x.id in used_a or y.id in used_b:
            continue
        used_a.add(x.id)
        used_b.add(y.id)
        shared = set(d["shared"])
        da, pa = _mentions(db, a.id, x.page_no)
        dbb, pb = _mentions(db, b.id, y.page_no)
        pairs.append({
            "score": score, "shared": ev_chips(shared),
            "only_a": ev_chips(set(x.tags) - shared), "only_b": ev_chips(set(y.tags) - shared),
            "a": {"page": x.page_no, "excerpt": event_excerpt(x.text, x.spans, shared)},
            "b": {"page": y.page_no, "excerpt": event_excerpt(y.text, y.spans, shared)},
            "same_report": same_report(x.text, y.text),
            "dates": [_date_label(v) for v in sorted(da & dbb)],
            "places": [place_label(v) for v in sorted(pa & pb)],
        })
        if len(pairs) >= 8:
            break

    types = r.get("clusters", [])
    ta = {t["id"] for t in types for i in t["accounts"] if i in {x.id for x in acc_a}}
    tb = {t["id"] for t in types for i in t["accounts"] if i in {y.id for y in acc_b}}
    both_types = [{"id": t["id"], "name": type_name(t)} for t in types if t["id"] in ta & tb]

    def norm(s):
        return (s or "").strip().lower()

    facts = [
        {"label": "Agency", "a": a.agency or "—", "b": b.agency or "—", "same": bool(a.agency) and norm(a.agency) == norm(b.agency)},
        {"label": "Incident date", "a": a.incident_date_raw or (str(a.incident_year) if a.incident_year else "—"),
         "b": b.incident_date_raw or (str(b.incident_year) if b.incident_year else "—"),
         "same": bool(a.incident_year) and a.incident_year == b.incident_year},
        {"label": "Location", "a": a.incident_location or "—", "b": b.incident_location or "—",
         "same": bool(a.incident_location) and norm(a.incident_location) == norm(b.incident_location)},
        {"label": "Released", "a": a.release.label if a.release else "—", "b": b.release.label if b.release else "—",
         "same": bool(a.release_id) and a.release_id == b.release_id},
        {"label": "Format", "a": _fmt(a), "b": _fmt(b), "same": False},
        {"label": "Sighting accounts found", "a": str(len(acc_a)), "b": str(len(acc_b)), "same": False},
    ]
    return {
        "a": a, "b": b, "link": link, "pairs": pairs, "facts": facts, "both_types": both_types,
        "n_a": len(acc_a), "n_b": len(acc_b),
        "cross_agency": bool(a.agency and b.agency and a.agency != b.agency),
        "years_apart": abs(a.incident_year - b.incident_year) if a.incident_year and b.incident_year else None,
    }


def _fmt(d: Document) -> str:
    s = {"pdf": "PDF", "video": "Video", "image": "Image", "audio": "Audio"}.get(d.media_type, d.media_type)
    return f"{s}, {d.page_count} page{'s' if d.page_count != 1 else ''}" if d.page_count else s


def _date_label(v: str) -> str:
    from datetime import date

    try:
        if len(v) == 7:
            return date.fromisoformat(v + "-01").strftime("%B %Y")
        return date.fromisoformat(v).strftime("%-d %B %Y")
    except ValueError:
        return v


def links_page(db: Session) -> dict:
    """All connections, each with its best-matching pair of accounts."""
    r = load_results(db)
    links = _link_views(db, r.get("links", []))
    return {"links": links, "computed_at": r.get("computed_at"),
            "same_report": sum(1 for l in links if l.get("same_report"))}


def _link_views(db: Session, links: list[dict]) -> list[dict]:
    titles = doc_titles(db, {x for l in links for x in (l["a"], l["b"])})
    accs = _accounts(db, {x for l in links for x in (l.get("acc_a"), l.get("acc_b")) if x})
    out = []
    for l in links:
        if l["a"] not in titles or l["b"] not in titles:
            continue
        xa, xb = accs.get(l.get("acc_a")), accs.get(l.get("acc_b"))
        shared = set(l.get("shared", []))
        out.append({**l, "da": titles[l["a"]], "db": titles[l["b"]], "chips": ev_chips(shared),
                    "ea": {"page": xa.page_no, "excerpt": event_excerpt(xa.text, xa.spans, shared, width=360)} if xa else None,
                    "eb": {"page": xb.page_no, "excerpt": event_excerpt(xb.text, xb.spans, shared, width=360)} if xb else None})
    return out
