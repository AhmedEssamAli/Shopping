"""Wires settings, store, browser profile and adapters together for the CLI, bot and web."""
from __future__ import annotations

import asyncio

from .adapters import build_adapters
from .browser import BrowserSession
from .memory import Memory, offer_key
from .config import Settings, load_settings
from .models import ItemRequest
from .parsing import parse_list
from .report import to_html, to_text
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
        self.adapters = build_adapters(self.settings, self.browser)
        self.last_results: dict[int, CompareResult] = {}  # per Telegram chat, for /push
        self.results: dict[str, CompareResult] = {}
        self.push_log: dict[str, list[str]] = {}  # result id -> what the web cart push reported  # by result id, for the web page's cart button

    def result_url(self, rid: str) -> str:
        token = f"?t={self.settings.web_token}" if self.settings.web_token else ""
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
        result = await compare(requests, self.settings, self.adapters, max_orders, exact_only, self.memory)
        self.store.record_offers(all_offers(result))
        rid = self.store.save_result("")
        self.results[rid] = result
        self._render(rid)
        while len(self.results) > 50:  # memory only; older pages stay viewable, just without the button
            self.results.pop(next(iter(self.results)))
        return result, self.text(rid), rid

    def text(self, rid: str, chat: bool = False) -> str:
        url = self.result_url(rid)
        if chat and not url.startswith("https://"):
            url = None  # http://127.0.0.1 does not open on the phone; Telegram gets a button instead
        return to_text(self.results[rid], self.settings, self.adapters, url, chat=chat)

    def _render(self, rid: str) -> None:
        result = self.results[rid]
        html = to_html(result, self.settings, self.adapters, base=f"/r/{rid}",
                       token=self.settings.web_token, learned=self.memory.learned())
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
        chosen = next((c for c in r.cards if c.key == card), None)
        if action == "pick" and chosen:
            self.memory.pick(r.request, chosen, r.cards)
        elif action == "reject" and m and not m.pinned and offer_key(m.offer) == offer:
            self.memory.reject(r.request, m.offer)
        elif action == "forget" and r.pinned:
            self.memory.forget(r.request)
        else:
            return "stale"
        assemble(result, self.settings, self.adapters, self.memory)
        self._render(rid)
        return "ok"

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
