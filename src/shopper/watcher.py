"""Price watches: alert when an item reaches your target price or its best price drops."""
from __future__ import annotations

import logging
import re

from .models import ItemRequest, Match
from .service import compare
from .store import Watch

log = logging.getLogger(__name__)
DROP_ALERT_PCT = 1.0
_TARGET = re.compile(r"^(.*?)\s*@\s*(\d+(?:[.,]\d+)?)\s*$")


def parse_watch(text: str) -> tuple[str, float | None]:
    """"Nescafe classic 200g @ 250" -> ("Nescafe classic 200g", 250.0)."""
    m = _TARGET.match(text.strip())
    if m:
        return m.group(1).strip(), float(m.group(2).replace(",", "."))
    return text.strip(), None


async def best_exact(app, query: str) -> Match | None:
    """Cheapest exact match for one unit of ``query`` across the enabled platforms."""
    result = await compare([ItemRequest(query, 1)], app.settings, app.adapters, max_orders=1)
    exact = [m for m in result.items[0].matches.values() if m and m.exact]
    return min(exact, key=lambda m: m.offer.price) if exact else None


def alert_for(w: Watch, m: Match, label: str) -> str | None:
    price = m.offer.price
    where = f"{label}: {m.offer.name} @ EGP {price:,.2f}\n{m.offer.url}"
    if w.target is not None and price <= w.target and (w.last_price is None or w.last_price > w.target):
        return f"TARGET REACHED for “{w.query}” (≤ {w.target:,.2f})\n{where}"
    if w.last_price is not None and price < w.last_price * (1 - DROP_ALERT_PCT / 100):
        return f"PRICE DROP for “{w.query}”: {w.last_price:,.2f} → {price:,.2f}\n{where}"
    return None


async def check_watches(app, chat_id: int | None = None) -> list[tuple[int, str]]:
    """Re-price every watch, store the new price, and return (chat_id, message) alerts."""
    alerts = []
    for w in app.store.list_watches(chat_id):
        try:
            m = await best_exact(app, w.query)
        except Exception:
            log.exception("watch %s failed", w.id)
            continue
        if not m:
            continue
        label = app.settings.platforms[m.offer.platform].label if m.offer.platform in app.settings.platforms else m.offer.platform
        msg = alert_for(w, m, label)
        app.store.update_watch(w.id, m.offer.price, m.offer.platform, m.offer.url)
        if msg:
            alerts.append((w.chat_id, msg))
    return alerts
