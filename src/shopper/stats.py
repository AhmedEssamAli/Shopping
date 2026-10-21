"""The spec's measures, read from the event log (see docs/spec-bulk-ordering-and-ui.md).

- time from the review step to "Compare" (how long checking a list takes)
- quantity changes per comparison (each one would have been a new search before)
- share of split plans whose orders were all ticked "Done"
"""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median

from .models import now_utc


def measures(events: list[tuple[str, str, str, str]], days: int = 30) -> dict:
    since = (now_utc() - timedelta(days=days)).isoformat()
    ev = [(datetime.fromisoformat(at), kind, rid, detail) for at, kind, rid, detail in events if at >= since]
    compares = [(at, rid, detail) for at, kind, rid, detail in ev if kind == "compare"]
    review_secs = []
    for at, kind, _, _ in ev:
        if kind == "review":
            nxt = next((c for c, _, _ in compares if c >= at), None)
            if nxt and (nxt - at).total_seconds() < 3600:
                review_secs.append((nxt - at).total_seconds())
    qty = sum(1 for _, kind, _, _ in ev if kind == "qty")
    done: dict[str, set[str]] = {}
    for _, kind, rid, shop in ev:
        if kind == "order_done":
            done.setdefault(rid, set()).add(shop)
        elif kind == "order_undone":
            done.setdefault(rid, set()).discard(shop)
    split = [(rid, int(detail.split(":")[1])) for _, rid, detail in compares if ":" in detail and int(detail.split(":")[1]) > 1]
    complete = sum(1 for rid, n in split if len(done.get(rid, ())) >= n)
    return {"days": days, "comparisons": len(compares),
            "review_to_compare_s": round(median(review_secs)) if review_secs else None,
            "qty_changes": qty, "qty_changes_per_comparison": round(qty / len(compares), 1) if compares else None,
            "split_plans": len(split), "split_plans_all_done": complete,
            "all_done_pct": round(100 * complete / len(split)) if split else None}
