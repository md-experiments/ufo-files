"""Server-rendered SVG/HTML charts.

Colours come from CSS classes (see style.css) so every chart follows the
light/dark theme. Every mark carries a <title> so hovering shows its value.
"""
from __future__ import annotations

import random
from html import escape

from markupsafe import Markup

MONTHS = "JFMAMJJASOND"
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]


def _e(s) -> str:
    return escape(str(s), quote=True)


def level(value: float, max_value: float, steps: int = 6) -> int:
    """Map a value onto the sequential ramp: 0 = none, 1..steps."""
    if not value or max_value <= 0:
        return 0
    return max(1, min(steps, int(-(-value / max_value * steps // 1))))


def timeline(years: list[dict], waves: list[dict], link: str = "/patterns/evidence?year={year}") -> Markup:
    """Reports per year with wave periods shaded and labelled."""
    if not years:
        return Markup('<p class="muted small">No dated reports yet.</p>')
    w, h, top, bottom, left = 1000, 244, 48, 26, 30
    n = len(years)
    step = (w - left) / n
    bw = max(1.0, step - 2)
    mx = max(y["count"] for y in years) or 1
    ch = h - top - bottom
    first = years[0]["year"]
    parts = [f'<svg class="chart timeline" viewBox="0 0 {w} {h}" role="img" aria-label="Reports per year">']
    # gridlines
    for frac in (0.5, 1.0):
        y = top + ch * (1 - frac)
        parts.append(f'<line class="grid" x1="{left}" x2="{w}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis" x="{left - 6}" y="{y + 4:.1f}" text-anchor="end">{round(mx * frac)}</text>')
    last_label_end = {0: -1e9, 1: -1e9}
    for wv in waves:
        x0 = left + (wv["start"] - first) * step - 1
        x1 = left + (wv["end"] - first + 1) * step + 1
        label = str(wv["start"]) if wv["start"] == wv["end"] else f'{wv["start"]}–{str(wv["end"])[2:]}'
        cx, half = (x0 + x1) / 2, len(label) * 3.6 + 4
        cx = min(max(cx, left + half), w - half)
        row = 0 if cx - half > last_label_end[0] else 1  # stagger labels that would collide
        last_label_end[row] = cx + half
        ly = top - 8 - row * 16
        parts.append(f'<rect class="wave-band" x="{x0:.1f}" y="{ly - 12}" width="{x1 - x0:.1f}" height="{top + ch - ly + 12}" rx="4"/>')
        parts.append(f'<text class="wave-label" x="{cx:.1f}" y="{ly}" text-anchor="middle">{label}</text>')
    for i, y in enumerate(years):
        c = y["count"]
        if c <= 0:
            continue
        bh = max(1.5, ch * c / mx)
        x = left + i * step + (step - bw) / 2
        yy = top + ch - bh
        in_wave = any(wv["start"] <= y["year"] <= wv["end"] for wv in waves)
        cls = "bar hot" if in_wave else "bar"
        href = link.format(year=y["year"])
        parts.append(
            f'<a href="{href}"><rect class="{cls}" x="{x:.1f}" y="{yy:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="{min(2, bw / 2):.1f}">'
            f'<title>{y["year"]}: {c:.0f} report{"s" if round(c) != 1 else ""}</title></rect></a>'
        )
    parts.append(f'<line class="baseline" x1="{left}" x2="{w}" y1="{top + ch}" y2="{top + ch}"/>')
    for i, y in enumerate(years):
        if y["year"] % 10 == 0:
            x = left + i * step + step / 2
            parts.append(f'<text class="axis" x="{x:.1f}" y="{h - 8}" text-anchor="middle">{y["year"]}</text>')
    parts.append("</svg>")
    return Markup("".join(parts))


def month_bars(counts: list[float], peak: int | None = None, label: str = "") -> Markup:
    w, h, pad = 240, 70, 14
    mx = max(counts) if counts and max(counts) > 0 else 1
    step = w / 12
    parts = [f'<svg class="chart months" viewBox="0 0 {w} {h}" role="img" aria-label="{_e(label)} reports by month">']
    for i, c in enumerate(counts):
        bh = (h - pad - 6) * c / mx if c else 0
        x = i * step + 2
        cls = "bar hot" if peak == i + 1 else "bar"
        if bh:
            parts.append(f'<rect class="{cls}" x="{x:.1f}" y="{h - pad - bh:.1f}" width="{step - 4:.1f}" height="{bh:.1f}" rx="2">'
                         f'<title>{MONTH_NAMES[i]}: {c:.0f}</title></rect>')
        parts.append(f'<text class="axis" x="{x + (step - 4) / 2:.1f}" y="{h - 2}" text-anchor="middle">{MONTHS[i]}</text>')
    parts.append(f'<line class="baseline" x1="0" x2="{w}" y1="{h - pad}" y2="{h - pad}"/></svg>')
    return Markup("".join(parts))


def decade_spark(decades: dict[str, int], all_decades: list[str]) -> Markup:
    if not all_decades:
        return Markup("")
    w, h = 120, 28
    step = w / len(all_decades)
    mx = max(decades.values()) if decades else 1
    parts = [f'<svg class="chart spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">']
    for i, d in enumerate(all_decades):
        c = decades.get(d, 0)
        bh = (h - 2) * c / mx if c else 0
        if bh:
            parts.append(f'<rect class="bar" x="{i * step + 1:.1f}" y="{h - bh:.1f}" width="{step - 2:.1f}" height="{bh:.1f}" rx="1.5">'
                         f'<title>{d}: {c}</title></rect>')
    parts.append(f'<line class="baseline" x1="0" x2="{w}" y1="{h - 0.5}" y2="{h - 0.5}"/></svg>')
    return Markup("".join(parts))


def heatmap(row_labels: list[tuple[str, str]], col_labels: list[str], values: list[list[float]],
            tips: list[list[str]], per_row: bool = True, row_link: str | None = None) -> Markup:
    """An HTML grid heatmap. ``row_labels`` are (key, label) pairs."""
    gmax = max((v for row in values for v in row), default=0) or 1
    cols = len(col_labels)
    parts = [f'<div class="heat" style="--cols:{cols}" role="table">',
             '<div class="heat-row heat-head" role="row"><span role="columnheader"></span>']
    parts += [f'<span class="heat-col" role="columnheader">{_e(c)}</span>' for c in col_labels]
    parts.append("</div>")
    for (key, lab), row, tip_row in zip(row_labels, values, tips):
        mx = (max(row) or 1) if per_row else gmax
        name = f'<a href="{row_link.format(key=key)}">{_e(lab)}</a>' if row_link else _e(lab)
        parts.append(f'<div class="heat-row" role="row"><span class="heat-label" role="rowheader">{name}</span>')
        for v, t in zip(row, tip_row):
            parts.append(f'<span class="heat-cell q{level(v, mx)}" role="cell" data-tip="{_e(t)}" title="{_e(t)}"></span>')
        parts.append("</div>")
    parts.append("</div>")
    return Markup("".join(parts))


def us_tilemap(grid: list[dict], link: str = "/patterns/evidence?place=US-{code}") -> Markup:
    size, gap = 44, 4
    cols = max(g["col"] for g in grid) + 1
    rows = max(g["row"] for g in grid) + 1
    w, h = cols * (size + gap), rows * (size + gap)
    mx = max(g["count"] for g in grid) or 1
    parts = [f'<svg class="chart tilemap" viewBox="0 0 {w} {h}" role="img" aria-label="Sighting pages mentioning each U.S. state">']
    for g in grid:
        x, y = g["col"] * (size + gap), g["row"] * (size + gap)
        q = level(g["count"], mx)
        tip = f'{g["name"]}: {g["count"]} sighting page{"s" if g["count"] != 1 else ""}'
        inner = (f'<rect class="state q{q}" x="{x}" y="{y}" width="{size}" height="{size}" rx="6"><title>{_e(tip)}</title></rect>'
                 f'<text class="state-label{" on-dark" if q >= 4 else ""}" x="{x + size / 2}" y="{y + size / 2 + 4}" text-anchor="middle">{g["code"]}</text>')
        parts.append(f'<a href="{link.format(code=g["code"])}">{inner}</a>' if g["count"] else inner)
    parts.append("</svg>")
    return Markup("".join(parts))


def case_map(points: list[dict], clusters: list[dict], titles: dict[int, str], max_badges: int = 14) -> Markup:
    """Every record as a dot; similar records sit close together. Cluster
    numbers are drawn at their centres; hovering a cluster highlights it."""
    w, h = 1000, 620
    parts = [f'<svg class="chart casemap" viewBox="0 0 {w} {h}" role="img" aria-label="Map of cases: similar records are close together">']
    for p in points:
        x, y = p["x"] * w, p["y"] * h
        c = p.get("cluster")
        cls = "pt in" if c else "pt"
        attr = f' data-c="{c}"' if c else ""
        title = _e(titles.get(p["id"], ""))
        parts.append(f'<a href="/documents/{p["id"]}"><circle class="{cls}"{attr} cx="{x:.1f}" cy="{y:.1f}" r="5">'
                     f'<title>{title}</title></circle></a>')
    for c in clusters[:max_badges]:
        x, y = c["centroid"][0] * w, c["centroid"][1] * h
        parts.append(f'<g class="badge" data-c="{c["id"]}" tabindex="0"><circle cx="{x:.1f}" cy="{y:.1f}" r="13"/>'
                     f'<text x="{x:.1f}" y="{y + 4.5:.1f}" text-anchor="middle">{c["id"]}</text>'
                     f'<title>{_e(c["name"])} ({c["size"]} records)</title></g>')
    parts.append("</svg>")
    return Markup("".join(parts))


def strip_plot(rows: list[dict], lo: int = 1940, hi: int = 2026) -> Markup:
    """One row per release; each record is a dot at its incident year."""
    left, w, rh = 150, 1000, 34
    h = rh * len(rows) + 26
    span = hi - lo
    rng = random.Random(3)
    parts = [f'<svg class="chart strip" viewBox="0 0 {w} {h}" role="img" aria-label="Incident years of the records in each release">']
    for dec in range(lo, hi + 1, 10):
        x = left + (w - left - 10) * (dec - lo) / span
        parts.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{h - 22}"/>'
                     f'<text class="axis" x="{x:.1f}" y="{h - 6}" text-anchor="middle">{dec}</text>')
    for i, r in enumerate(rows):
        cy = i * rh + rh / 2
        parts.append(f'<text class="row-label" x="{left - 12}" y="{cy + 4:.1f}" text-anchor="end">{_e(r["label"])}</text>')
        for d in r["docs"]:
            if not d["year"] or d["year"] < lo:
                continue
            x = left + (w - left - 10) * (d["year"] - lo) / span
            y = cy + rng.uniform(-rh * 0.3, rh * 0.3)
            parts.append(f'<a href="/documents/{d["id"]}"><circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="3.5">'
                         f'<title>{_e(d["title"])} ({d["year"]})</title></circle></a>')
    parts.append("</svg>")
    return Markup("".join(parts))


def stacked_bar(segments: list[dict], total: int) -> Markup:
    """A 100% bar. segments: {label, n, cls}."""
    parts = ['<div class="stack" role="img">']
    for s in segments:
        if not s["n"]:
            continue
        pct = 100 * s["n"] / total
        tip = f'{s["label"]}: {s["n"]} ({pct:.0f}%)'
        parts.append(f'<span class="stack-seg {s["cls"]}" style="flex:{s["n"]}" data-tip="{_e(tip)}" title="{_e(tip)}"></span>')
    parts.append("</div>")
    return Markup("".join(parts))
