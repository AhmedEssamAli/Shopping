"""Wires settings, store, browser profile and adapters together for the CLI, bot and web."""
from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from .adapters import build_adapters
from .adapters.appshop import PriceBook
from .browser import BrowserSession
from .learner import Learner
from .memory import Memory, offer_id, offer_key
from .config import Settings, load_settings
from .models import ItemRequest
from .parsing import parse_list
from .report import to_chat, to_html, to_text
from .service import CompareResult, all_offers, assemble, compare
from .store import Store


HOUSEHOLD = 0  # one shared list for every allowed Telegram chat and the web page


class App:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.store = Store(self.settings.db_path)
        self.memory = Memory(self.store.db)
        self.store.merge_lists_into(HOUSEHOLD)
        needs_browser = any(p.status == "browser" for p in self.settings.enabled())
        self.browser = BrowserSession(self.settings) if needs_browser else None
        self.learner = Learner(self.store.db)  # learns from your answers which matches are right
        self.prices = PriceBook(self.store.db)  # prices typed in from app-only shops
        self.adapters = build_adapters(self.settings, self.browser, self.prices)
        self.last_rid: dict[int, str] = {}  # per Telegram chat, for /push
        self.results: dict[str, CompareResult] = {}
        self.push_log: dict[str, list[str]] = {}  # result id -> what its cart push reported, from bot or web
        for name, a in self.adapters.items():  # the delivery area you chose on the home page
            saved = self.store.pref(f"area:{name}")
            if saved and hasattr(a, "use_area"):
                a.use_area(json.loads(saved))

    async def set_area(self, shop: str, district_id: str) -> dict | None:
        """Choose the shop's delivery district (Seoudi). Returns the area, or None if unknown."""
        a = self.adapters.get(shop)
        if not a or not hasattr(a, "areas"):
            return None
        area = next((x for x in await a.areas() if x["id"] == district_id), None)
        if area:
            a.use_area(area)
            self.store.set_pref(f"area:{shop}", json.dumps(area))
        return area

    def result_url(self, rid: str) -> str:
        token = f"?t={quote(self.settings.web_token)}" if self.settings.web_token else ""
        return f"{self.settings.web_base_url}/r/{rid}{token}"

    def parse(self, text: str) -> list[ItemRequest]:
        return parse_list(text, self.settings.anthropic_key)

    async def parse_async(self, text: str) -> list[ItemRequest]:
        """The Claude call blocks; run it off the event loop so the bot and the page stay responsive."""
        return await asyncio.to_thread(self.parse, text)

    def pushable(self, result: CompareResult) -> list[str]:
        """Shops in the best plan whose carts shopper can fill."""
        best = result.best
        return [p for p in (best.platforms if best else []) if p in self.adapters and self.adapters[p].supports_cart_push]

    async def run(self, requests: list[ItemRequest], max_orders: int | None = None, exact_only: bool = False
                  ) -> tuple[CompareResult, str, str]:
        """Returns (result, chat text, saved result id)."""
        result = await compare(requests, self.settings, self.adapters, max_orders, exact_only, self.memory, self.learner)
        self.store.record_offers(all_offers(result))
        rid = self.store.save_result("")
        self.results[rid] = result
        self._render(rid)
        while len(self.results) > 50:  # memory only; older pages stay viewable, just without the button
            self.results.pop(next(iter(self.results)))
        return result, self.text(rid), rid

    def text(self, rid: str) -> str:
        return to_text(self.results[rid], self.settings, self.adapters, self.result_url(rid))

    def chat_text(self, rid: str) -> str:
        """Telegram HTML. The table link only when it opens on a phone (https over Tailscale)."""
        url = self.result_url(rid)
        return to_chat(self.results[rid], self.settings, self.adapters, url if url.startswith("https://") else None)

    def _render(self, rid: str) -> None:
        result = self.results[rid]
        html = to_html(result, self.settings, self.adapters, base=f"/r/{rid}",
                       token=self.settings.web_token, learned={**self.memory.learned(), "answers": self.learner.answers})
        n = len(result.items)
        lines = ", ".join(r.request.query for r in result.items[:3]) + ("…" if n > 3 else "")
        total = f"EGP {result.best.total:,.2f}" if result.best else "no plan"
        self.store.update_result(rid, html, f"{n} item{'s' if n != 1 else ''}: {lines} — {total}")

    # The household list, shared by Telegram and the web page.
    def list_items(self) -> list[ItemRequest]:
        return self.store.get_list(HOUSEHOLD)

    def add_to_list(self, items: list[ItemRequest]) -> None:
        self.store.add_to_list(HOUSEHOLD, items)

    def save_list(self, items: list[ItemRequest]) -> None:
        self.store.clear_list(HOUSEHOLD)
        self.store.add_to_list(HOUSEHOLD, items)

    def remove_from_list(self, index: int, expect: str | None = None) -> bool:
        """``expect``: the line's text, so a stale button cannot remove a line someone else moved."""
        items = self.list_items()
        if expect is not None and not (0 <= index < len(items) and items[index].query == expect):
            return False
        return self.store.remove_from_list(HOUSEHOLD, index)

    def clear_list(self) -> None:
        self.store.clear_list(HOUSEHOLD)

    def feedback(self, rid: str, action: str, item: int, card: str | None = None, platform: str | None = None,
                 offer: str | None = None) -> str:
        """Apply a pick / reject / forget, then re-match the offers already fetched (no new
        search) and re-render the page.

        Buttons name a card key or an offer key, never a position, so a double tap or a
        stale page cannot choose or reject a different product. Returns "ok", "stale"
        (nothing matched; nothing changed) or "gone" (the result is no longer in memory).
        """
        result = self.results.get(rid)
        if result is None:
            return "gone"
        if not 0 <= item < len(result.items):
            return "stale"
        r = result.items[item]
        m = r.matches.get(platform) if platform else None
        if action == "confirm" and m and not r.pinned and offer_key(m.offer) == offer:
            # "Right product? Yes": choose the card this offer belongs to, as if you had picked it.
            card = next((c.key for c in r.cards if any(offer_id(o) == offer_id(m.offer) for o in c.offers)), None)
            action = "pick"
        elif action == "deny":
            action = "reject"  # "Right product? No" is "not this"
        chosen = next((c for c in r.cards if c.key == card), None)
        pool = [o for offers in r.candidates.values() for o in offers]
        cheapest = min((o.price for o in pool), default=None)
        avoid = r.recall.avoid if r.recall else frozenset()
        if action == "pick" and chosen and not r.pinned:  # a second tap on "This one" is a no-op
            for o in chosen.per_platform().values():
                self.learner.learn(r.request, o, True, cheapest, avoid)
            for other in r.cards:  # exact products you passed over are examples of "not this one"
                if other is not chosen and other.exact:
                    self.learner.learn(r.request, other.cheapest(), False, cheapest, avoid)
            self.memory.pick(r.request, chosen, r.cards)
        elif action == "reject" and m and not m.pinned and offer_key(m.offer) == offer:
            self.learner.learn(r.request, m.offer, False, cheapest, avoid)
            self.memory.reject(r.request, m.offer)
        elif action == "forget" and r.pinned:
            self.memory.forget(r.request)
        else:
            return "stale"
        assemble(result, self.settings, self.adapters, self.memory, self.learner)
        self._render(rid)
        return "ok"

    async def push_once(self, rid: str) -> list[str]:
        """Push a result's plan at most once, from any interface: a double tap, a refresh, or
        the bot and the page together must not add everything twice."""
        result = self.results.get(rid)
        if result is None:
            return ["This comparison is no longer open. Compare the list again."]
        if rid in self.push_log:
            return ["Already added to your carts:", *self.push_log[rid]]
        if any(r.needs_pick for r in result.items):
            return await self.push_carts(result)  # refuses, and is not recorded, so it can run after the pick
        self.push_log[rid] = ["Adding to your carts now…"]  # claimed before the first await
        try:
            self.push_log[rid] = await self.push_carts(result)
        except Exception:
            del self.push_log[rid]
            raise
        return self.push_log[rid]

    async def push_carts(self, result: CompareResult) -> list[str]:
        """Put the best plan into your real carts where the platform allows it; otherwise say how."""
        best = result.best
        if not best:
            return ["Nothing to push: no plan."]
        pending = [r.request.query for r in result.items if r.needs_pick]
        if pending:
            return ["Not added: choose your product first for " + "; ".join(pending) + "."]
        out = []
        for platform in best.platforms:
            a = self.adapters.get(platform)
            lines = [(m.offer, m.request.qty) for m in best.assignments.values() if m.offer.platform == platform]
            name = a.label if a else platform
            if a and a.supports_cart_push:
                try:
                    out += [f"{name}: {x}" for x in await a.add_to_cart(lines)]
                except Exception as e:
                    out.append(f"{name}: cart push failed: {type(e).__name__}: {e}")
            else:
                link = a.cart_link(lines) if a else None
                out.append(f"{name}: no cart push; " + (f"open {link}" if link else "add the items from the product links"))
        return out

    async def close(self) -> None:
        for a in self.adapters.values():
            await a.close()
        if self.browser:
            await self.browser.close()
