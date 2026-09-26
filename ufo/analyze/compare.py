"""Shared helpers for the profile summaries: what a subset of records or
accounts reports more often than the rest ("lift"), and how a subset splits
along the record facets (agency, decade, kind, shape...)."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Callable, Iterable

MIN_GROUP = 8  # a subset with fewer members is listed, not compared


def lift_rows(inside: Iterable[Iterable[str]], others: Iterable[Iterable[str]], label: Callable[[str], str],
              min_count: int = 3, min_lift: float = 1.3, limit: int = 8) -> list[dict]:
    """Items the ``inside`` members report more often than the ``others`` do.

    Each argument is one item set per member (page, account or record).
    Rows: key, label, count and share among the inside members, and lift:
    their share divided by the others' share. An item none of the others
    report gets the lift it would have if one of them did, so it ranks high
    without being infinite."""
    inside = [set(x) for x in inside]
    others = [set(x) for x in others]
    n_in, n_out = len(inside), len(others)
    if n_in < MIN_GROUP or n_out < MIN_GROUP:
        return []
    c_in = Counter(k for s in inside for k in s)
    c_out = Counter(k for s in others for k in s)
    rows = []
    for k, c in c_in.items():
        if c < min_count:
            continue
        base = max(c_out[k], 1) / n_out
        lift = (c / n_in) / base
        if lift >= min_lift:
            rows.append({"key": k, "label": label(k), "count": c, "share": round(c / n_in, 3),
                         "others": c_out[k], "lift": round(lift, 1)})
    rows.sort(key=lambda r: (-r["lift"], -r["count"]))
    return rows[:limit]


def top_rows(inside: Iterable[Iterable[str]], label: Callable[[str], str], limit: int = 8) -> list[dict]:
    """The most common items among the ``inside`` members."""
    inside = [set(x) for x in inside]
    c = Counter(k for s in inside for k in s)
    return [{"key": k, "label": label(k), "count": n, "share": round(n / len(inside), 3)}
            for k, n in c.most_common(limit)]


def split_by(members: Iterable[tuple[str, str | None]], groups: list[str]) -> list[dict]:
    """How a categorical attribute splits into ``groups``.

    ``members`` are (group, category) pairs, e.g. ("unresolved", "FBI").
    Returns one row per category with counts per group, largest first."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for group, cat in members:
        if cat is not None:
            counts[cat][group] += 1
    rows = []
    for cat, c in counts.items():
        total = sum(c.values())
        rows.append({"value": cat, "total": total, "counts": {g: c.get(g, 0) for g in groups},
                     "shares": {g: round(c.get(g, 0) / total, 3) for g in groups}})
    rows.sort(key=lambda r: -r["total"])
    return rows


def decade_of(year: int | None) -> str | None:
    if not year or year < 1940:
        return None
    return f"{year // 10 * 10}s"
