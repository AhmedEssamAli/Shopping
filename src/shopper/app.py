"""Wires settings, store, browser profile and adapters together for the CLI, bot and web."""
from __future__ import annotations

from .adapters import build_adapters
from .browser import BrowserSession
from .config import Settings, load_settings
from .models import ItemRequest
from .parsing import parse_list
from .report import to_html, to_text
from .service import CompareResult, all_offers, compare
from .store import Store


class App:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.store = Store(self.settings.db_path)
        needs_browser = any(p.status == "browser" for p in self.settings.enabled())
        self.browser = BrowserSession(self.settings) if needs_browser else None
        self.adapters = build_adapters(self.settings, self.browser)
        self.last_results: dict[int, CompareResult] = {}  # per Telegram chat, for /push

    def result_url(self, rid: str) -> str:
        token = f"?t={self.settings.web_token}" if self.settings.web_token else ""
        return f"{self.settings.web_base_url}/r/{rid}{token}"

    def parse(self, text: str) -> list[ItemRequest]:
        return parse_list(text, self.settings.anthropic_key)

    async def run(self, requests: list[ItemRequest], max_orders: int | None = None, exact_only: bool = False
                  ) -> tuple[CompareResult, str, str]:
        """Returns (result, chat text, saved result id)."""
        result = await compare(requests, self.settings, self.adapters, max_orders, exact_only)
        self.store.record_offers(all_offers(result))
        rid = self.store.save_result(to_html(result, self.settings, self.adapters))
        text = to_text(result, self.settings, self.adapters, self.result_url(rid))
        return result, text, rid

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
