"""The hierarchy of events: how the episodes split into classes and
subcategories, and a map that draws that split.

The tree has two levels under the root: the event class (seen in the sky,
detected by instruments, close encounter, physical evidence, from space, no
event) and a subcategory derived from the episode's details (over water,
engine interference, landed, occupants ...). Each node carries counts,
outcomes, decades, agencies, the details its members report most, and a
few example episodes.

The map is a circle packing: one circle per class, the subcategory circles
inside it, and one dot per episode inside those, so the hierarchy and the
relative sizes are visible at once. It is deterministic.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from .compare import decade_of
from .episodes import EVENTS, SUBCATS, detail_label

POINT = 1.0  # dot radius in layout units
PAD_SUB = 2.2
PAD_CLASS = 3.0
GOLDEN = math.pi * (3 - math.sqrt(5))


def _node(members: list[dict], docs: dict, defining: set[str], k_examples: int = 3) -> dict:
    records = Counter(m["doc"] for m in members)
    outcomes = Counter(m.get("outcome") or "not_stated" for m in members)
    decades = Counter(d for d in (decade_of(m.get("year")) for m in members) if d)
    agencies = Counter(docs[m["doc"]]["agency"] for m in members if docs.get(m["doc"], {}).get("agency"))
    details = Counter(d for m in members for d in set(m.get("details") or []) if d not in defining)
    # examples from different records; hand-read ones with the most detail first
    seen, examples = set(), []
    for m in sorted(members, key=lambda m: ({"curated": 0, "llm": 1, "rules": 2}.get(m.get("source"), 3),
                                            -len(m.get("details") or []), m["id"])):
        if m["doc"] in seen:
            continue
        seen.add(m["doc"])
        examples.append(m["id"])
        if len(examples) >= k_examples:
            break
    years = [m["year"] for m in members if m.get("year")]
    return {
        "count": len(members), "records": len(records),
        "outcomes": dict(outcomes),
        "decades": [{"decade": d, "count": n} for d, n in sorted(decades.items())],
        "agencies": [{"agency": a, "count": n} for a, n in agencies.most_common(4)],
        "top_details": [{"key": d, "label": detail_label(d), "count": n, "share": round(n / len(members), 3)}
                        for d, n in details.most_common(6)] if members else [],
        "examples": examples,
        "years": [min(years), max(years)] if years else None,
        "sources": dict(Counter(m.get("source") or "rules" for m in members)),
    }


def hierarchy_summary(episodes: list[dict], docs: dict) -> dict:
    """``episodes``: dicts with id, doc, event, sub, details, year, outcome,
    source. ``docs``: id -> {agency, title, record_id}."""
    total = len(episodes)
    by_class: dict[str, list[dict]] = defaultdict(list)
    for e in episodes:
        by_class[e["event"]].append(e)
    classes = []
    for key, (label, desc) in EVENTS.items():
        members = by_class.get(key, [])
        by_sub: dict[str, list[dict]] = defaultdict(list)
        for m in members:
            by_sub[m["sub"]].append(m)
        subs = []
        for s in SUBCATS[key]:
            sm = by_sub.get(s.key, [])
            if not sm:
                continue
            subs.append({"key": s.key, "label": s.label, "description": s.description,
                         "share": round(len(sm) / len(members), 3) if members else 0,
                         **_node(sm, docs, set(s.any))})
        classes.append({"key": key, "label": label, "description": desc,
                        "share": round(len(members) / total, 3) if total else 0,
                        **_node(members, docs, set()), "subs": subs})
    decades = sorted({d for c in classes for d in (x["decade"] for x in c["decades"])})
    per_decade = Counter(d for d in (decade_of(e.get("year")) for e in episodes) if d)
    matrix = []
    for c in classes:
        if not c["count"]:
            continue
        counts = {x["decade"]: x["count"] for x in c["decades"]}
        matrix.append({"key": c["key"], "label": c["label"],
                       "cells": [{"decade": d, "count": counts.get(d, 0),
                                  "share": round(counts.get(d, 0) / per_decade[d], 4) if per_decade[d] else 0}
                                 for d in decades]})
    return {
        "total": total,
        "records": len({e["doc"] for e in episodes}),
        "sources": dict(Counter(e.get("source") or "rules" for e in episodes)),
        "outcomes": dict(Counter(e.get("outcome") or "not_stated" for e in episodes)),
        "classes": classes,
        "decades": decades,
        "decade_totals": [per_decade[d] for d in decades],
        "decade_matrix": matrix,
        "map": pack_layout(episodes),
    }


# ---------------------------------------------------------------------------
# Circle packing

def pack_circles(radii: list[float], pad: float = 0.0) -> list[tuple[float, float]]:
    """Centres for circles of the given radii, packed tightly around the
    origin, largest first. Deterministic."""
    order = sorted(range(len(radii)), key=lambda i: -radii[i])
    placed: list[tuple[float, float, float]] = []  # x, y, r
    out: list[tuple[float, float] | None] = [None] * len(radii)
    for n, i in enumerate(order):
        r = radii[i]
        if n == 0:
            pos = (0.0, 0.0)
        elif n == 1:
            pos = (placed[0][2] + r + pad, 0.0)
        else:
            best, best_d = None, float("inf")
            for (px, py, pr) in placed:
                dist = pr + r + pad
                for step in range(72):
                    a = step * math.pi / 36
                    x, y = px + dist * math.cos(a), py + dist * math.sin(a)
                    if any((x - qx) ** 2 + (y - qy) ** 2 < (r + qr + pad - 1e-9) ** 2 for qx, qy, qr in placed):
                        continue
                    d = x * x + y * y
                    if d < best_d:
                        best, best_d = (x, y), d
            pos = best or (placed[-1][0] + placed[-1][2] + r + pad, 0.0)
        placed.append((pos[0], pos[1], r))
        out[i] = pos
    return [p or (0.0, 0.0) for p in out]


def _enclose(circles: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """An enclosing circle (centre x, y and radius) for (x, y, r) circles."""
    if not circles:
        return 0.0, 0.0, 1.0
    xs = [x - r for x, _, r in circles] + [x + r for x, _, r in circles]
    ys = [y - r for _, y, r in circles] + [y + r for _, y, r in circles]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    for _ in range(30):  # walk the centre toward the farthest circle; shrinks the radius
        far = max(circles, key=lambda c: math.hypot(c[0] - cx, c[1] - cy) + c[2])
        d = math.hypot(far[0] - cx, far[1] - cy)
        if d < 1e-9:
            break
        cx += (far[0] - cx) / d * 0.15 * d
        cy += (far[1] - cy) / d * 0.15 * d
    rad = max(math.hypot(x - cx, y - cy) + r for x, y, r in circles)
    return cx, cy, rad


def _sunflower(n: int, radius: float) -> list[tuple[float, float]]:
    pts = []
    for k in range(n):
        rr = radius * math.sqrt((k + 0.5) / n) if n > 1 else 0.0
        a = k * GOLDEN
        pts.append((rr * math.cos(a), rr * math.sin(a)))
    return pts


def pack_layout(episodes: list[dict]) -> dict:
    """Positions for the map. Coordinates are scaled so x runs 0..1 and y
    0..aspect; ``point`` is the dot radius in the same units."""
    groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for e in episodes:
        groups[e["event"]][e["sub"]].append(e)
    classes = []
    for ckey in EVENTS:
        subs = groups.get(ckey)
        if not subs:
            continue
        sub_keys = [s.key for s in SUBCATS[ckey] if s.key in subs]
        inner = [POINT * (1.25 * math.sqrt(len(subs[k])) + 1.0) for k in sub_keys]
        radii = [r + PAD_SUB for r in inner]
        centres = pack_circles(radii, pad=0.6)
        circ = [(x, y, r) for (x, y), r in zip(centres, radii)]
        cx, cy, cr = _enclose(circ)
        sub_nodes = []
        for k, (x, y), r, ri in zip(sub_keys, centres, radii, inner):
            pts = _sunflower(len(subs[k]), max(0.0, ri - POINT))
            sub_nodes.append({"key": k, "x": x - cx, "y": y - cy, "r": r,
                              "points": [(m["id"], x - cx + px, y - cy + py) for m, (px, py) in zip(subs[k], pts)]})
        classes.append({"key": ckey, "r": cr + PAD_CLASS, "subs": sub_nodes})
    if not classes:
        return {"classes": [], "points": [], "aspect": 0.6, "point": 0.01}
    centres = pack_circles([c["r"] for c in classes], pad=1.5)
    for c, (x, y) in zip(classes, centres):
        c["x"], c["y"] = x, y
    xs = [c["x"] - c["r"] for c in classes] + [c["x"] + c["r"] for c in classes]
    ys = [c["y"] - c["r"] for c in classes] + [c["y"] + c["r"] for c in classes]
    x0, y0, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    w = w or 1.0
    margin = 0.06 * w  # room for the labels at the edges
    x0, y0, w, h = x0 - margin, y0 - margin, w + 2 * margin, h + 2 * margin
    scale = 1.0 / w
    out_classes, points = [], []
    for c in classes:
        subs = []
        for s in c["subs"]:
            subs.append({"key": s["key"], "x": round((c["x"] + s["x"] - x0) * scale, 4),
                         "y": round((c["y"] + s["y"] - y0) * scale, 4), "r": round(s["r"] * scale, 4)})
            for eid, px, py in s["points"]:
                points.append({"id": eid, "x": round((c["x"] + px - x0) * scale, 4),
                               "y": round((c["y"] + py - y0) * scale, 4), "e": c["key"], "s": s["key"]})
        out_classes.append({"key": c["key"], "x": round((c["x"] - x0) * scale, 4), "y": round((c["y"] - y0) * scale, 4),
                            "r": round(c["r"] * scale, 4), "subs": subs})
    return {"classes": out_classes, "points": points, "aspect": round(h * scale, 4), "point": round(POINT * scale, 5)}
