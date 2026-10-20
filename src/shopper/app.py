"""Wires settings, store, browser profile and adapters together for the CLI, bot and web."""
from __future__ import annotations

from .adapters import build_adapters
from .browser import BrowserSession
from .memory import Memory
from .config import Settings, load_settings
from .models import ItemRequest
from .parsing import parse_list
from .report import to_html, to_text
from .service import CompareResult, all_offers, assemble, compare
from .store import Store


class App:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.store = Store(self.settings.db_path)
        self.memory = Memory(self.store.db)
        needs_browser = any(p.status == "browser" for p in self.settings.enabled())
        self.browser = BrowserSession(self.settings) if needs_browser else None
        self.adapters = build_adapters(self.settings, self.browser)
        self.last_results: dict[int, CompareResult] = {}  # per Telegram chat, for /push
        self.results: dict[str, CompareResult] = {}  # by result id, for the web page's cart button

    def result_url(self, rid: str) -> str:
        token = f"?t={self.settings.web_token}" if self.settings.web_token else ""
        return f"{self.settings.web_base_url}/r/{rid}{token}"

    def parse(self, text: str) -> list[ItemRequest]:
        return parse_list(text, self.settings.anthropic_key)

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
        text = to_text(result, self.settings, self.adapters, self.result_url(rid))
        return result, text, rid

    def _render(self, rid: str) -> None:
        html = to_html(self.results[rid], self.settings, self.adapters, base=f"/r/{rid}",
                       token=self.settings.web_token, learned=self.memory.learned())
        self.store.update_result(rid, html)

    def feedback(self, rid: str, action: str, item: int, card: int | None = None, platform: str | None = None) -> bool:
        """Apply a pick / reject / forget from the comparison page, then re-match the offers
        already fetched (no new search) and re-render the page. False if the result is gone."""
        result = self.results.get(rid)
        if result is None or not 0 <= item < len(result.items):
            return False
        r = result.items[item]
        if action == "pick" and card is not None and 0 <= card < len(r.cards):
            self.memory.pick(r.request, r.cards[card], r.cards)
        elif action == "reject" and platform and r.matches.get(platform):
            self.memory.reject(r.request, r.matches[platform].offer)
        elif action == "forget":
            self.memory.forget(r.request)
        else:
            return False
        assemble(result, self.settings, self.adapters, self.memory)
        self._render(rid)
        return True

    async def push_carts(self, result: CompareResult) -> list[str]:
        """Put the best plan into your real carts where the platform allows it; otherwise say how."""
        best = result.best
        if not best:
            return ["Nothing to push: no plan."]
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
