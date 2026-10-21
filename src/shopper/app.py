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
from .service import CompareResult, all_offers, assemble, compare, plan_options, search_line
from .store import Store


HOUSEHOLD = 0  # one shared list for every allowed Telegram chat and the web page


def _lines_key(lines) -> tuple:
    return tuple(sorted((o.platform, o.url, o.name, qty) for o, qty in lines))


def _plan_key(result) -> dict[str, tuple]:
    """Each shop's lines in the best plan, to see what a re-plan changed."""
    best = result.best
    if not best:
        return {}
    return {p: _lines_key([(m.offer, m.request.qty) for m in best.assignments.values() if m.offer.platform == p])
            for p in best.platforms}


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
        self.removed: dict[str, tuple[int, object]] = {}  # result id -> last line removed there, for Undo
        self.shop_pushes: dict[tuple[str, str], list[str]] = {}  # (result id, shop) -> what one order card's push did
        self.done: dict[str, set[str]] = {}  # result id -> shops whose order you ticked as placed
        self.pushed_lines: dict[tuple[str, str], tuple] = {}  # what each card push put in the cart
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
        self.store.log("compare", rid, f"{len(requests)}:{len(result.best.platforms) if result.best else 0}")
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
                       token=self.settings.web_token, learned={**self.memory.learned(), "answers": self.learner.answers},
                       options=plan_options(result, self.settings, self.adapters), undo=rid in self.removed,
                       orders={"done": self.done.get(rid, set()),
                               "pushed": {s: v for (r, s), v in self.shop_pushes.items() if r == rid},
                               "plan_pushed": self.push_log.get(rid)})
        n = len(result.items)
        lines = ", ".join(r.request.query for r in result.items[:3]) + ("…" if n > 3 else "")
        total = f"EGP {result.best.total:,.2f}" if result.best else "no plan"
        self.store.update_result(rid, html, f"{n} item{'s' if n != 1 else ''}: {lines} — {total}")

    # Change a comparison in place: quantities, lines, order limit. Re-plans from the offers
    # already fetched, so it takes milliseconds and searches no shop.
    def edit_line(self, rid: str, item: int, expect: str | None, qty: int | None = None, remove: bool = False) -> str:
        """Set a line's quantity, or remove it. ``expect`` is the line's text, so a stale page
        cannot change another line. Returns "ok", "stale" or "gone"."""
        result = self.results.get(rid)
        if result is None:
            return "gone"
        if not 0 <= item < len(result.items) or (expect is not None and result.items[item].request.query != expect):
            return "stale"
        if remove or (qty is not None and qty < 1):
            if len(result.items) == 1:
                return "stale"  # a comparison keeps at least one line
            self.removed[rid] = (item, result.items.pop(item))
        elif qty is not None:
            r = result.items[item]
            r.request = ItemRequest(r.request.query, min(qty, 999))
            self.store.log("qty", rid, r.request.query)
        self._replan(rid)
        return "ok"

    async def research_line(self, rid: str, item: int, expect: str | None, request: ItemRequest) -> str:
        """Search all shops again for one line, with the fields you adjusted. Returns "ok", "stale" or "gone"."""
        result = self.results.get(rid)
        if result is None:
            return "gone"
        if not 0 <= item < len(result.items) or (expect is not None and result.items[item].request.query != expect):
            return "stale"
        line, errors = await search_line(request, self.settings, self.adapters, result.platforms, self.memory)
        if result.items[item].request.query != (expect or result.items[item].request.query):
            return "stale"  # the line changed while the shops were searched
        result.items[item] = line
        for p, e in errors.items():
            result.errors.setdefault(p, e)
        self.store.record_offers([o for offers in line.candidates.values() for o in offers])
        self.store.log("research", rid, request.query)
        self._replan(rid)
        return "ok"

    def undo_remove(self, rid: str) -> bool:
        result, last = self.results.get(rid), self.removed.pop(rid, None)
        if result is None or last is None:
            return False
        i, line = last
        result.items.insert(min(i, len(result.items)), line)
        self._replan(rid)
        return True

    def set_orders(self, rid: str, limit: int) -> bool:
        result = self.results.get(rid)
        if result is None or not 1 <= limit <= 4:
            return False
        result.max_orders = limit
        self._replan(rid)
        return True

    def _replan(self, rid: str) -> None:
        """Plan again from the fetched offers. A shop's cart push and Done tick stay only while
        that shop's lines are unchanged; a whole-plan push stays only while the plan is unchanged."""
        result = self.results[rid]
        before = _plan_key(result)
        assemble(result, self.settings, self.adapters, learner=self.learner)
        after = _plan_key(result)
        if after != before:
            self.push_log.pop(rid, None)
        for shop in list(self.done.get(rid, ())):
            if before.get(shop) != after.get(shop):
                self.done[rid].discard(shop)
        for key in [k for k in self.shop_pushes if k[0] == rid]:
            if self.pushed_lines.get(key) != after.get(key[1]):
                self.shop_pushes.pop(key, None)
                self.pushed_lines.pop(key, None)
        self.store.log("plan", rid, str(len(result.best.platforms) if result.best else 0))
        self._render(rid)

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

    def named_lists(self) -> list[tuple[str, int]]:
        return self.store.named_lists()

    def save_named(self, name: str, items: list[ItemRequest] | None = None) -> bool:
        """Keep a named copy ("Weekly") of these lines, or of the household list."""
        name = " ".join(name.split())[:40]
        items = self.list_items() if items is None else items
        if not name or not items:
            return False
        self.store.save_named(name, items)
        return True

    def named(self, name: str) -> list[ItemRequest]:
        return self.store.get_named(name)

    def use_named(self, name: str) -> bool:
        """Make a saved list the household list."""
        items = self.named(name)
        if items:
            self.save_list(items)
        return bool(items)

    def delete_named(self, name: str) -> None:
        self.store.delete_named(name)

    def set_list_qty(self, index: int, qty: int, expect: str | None = None) -> bool:
        """Change a list line's quantity; below 1 removes the line."""
        items = self.list_items()
        if not 0 <= index < len(items) or (expect is not None and items[index].query != expect):
            return False
        if qty < 1:
            return self.store.remove_from_list(HOUSEHOLD, index)
        items[index] = ItemRequest(items[index].query, min(qty, 999))
        self.save_list(items)
        return True

    def feedback(self, rid: str, action: str, item: int, card: str | None = None, platform: str | None = None,
                 offer: str | None = None, line: str | None = None) -> str:
        """Apply a pick / reject / forget, then re-match the offers already fetched (no new
        search) and re-render the page.

        Buttons name a card key or an offer key, never a position, so a double tap or a
        stale page cannot choose or reject a different product. Returns "ok", "stale"
        (nothing matched; nothing changed) or "gone" (the result is no longer in memory).
        """
        result = self.results.get(rid)
        if result is None:
            return "gone"
        if not 0 <= item < len(result.items) or (line is not None and result.items[item].request.query != line):
            return "stale"  # lines can be removed, so a position alone may now name another line
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
            self.push_log[rid] = await self.push_carts(result, {s for r, s in self.shop_pushes if r == rid})
        except Exception:
            del self.push_log[rid]
            raise
        return self.push_log[rid]

    async def push_shop(self, rid: str, shop: str) -> list[str]:
        """One order card's "Add to cart": push that shop's lines once. Refused after a whole-plan push."""
        result = self.results.get(rid)
        if result is None:
            return ["This comparison is no longer open. Compare the list again."]
        if rid in self.push_log:
            return ["Already added with the whole plan:", *self.push_log[rid]]
        if (rid, shop) in self.shop_pushes:
            return self.shop_pushes[(rid, shop)]
        a, best = self.adapters.get(shop), result.best
        if not best or shop not in best.platforms or not (a and a.supports_cart_push):
            return [f"{a.label if a else shop}: shopper cannot fill this cart."]
        if any(r.needs_pick for r in result.items):
            return ["Not added: choose your product first for "
                    + "; ".join(r.request.query for r in result.items if r.needs_pick) + "."]
        self.shop_pushes[(rid, shop)] = ["Adding to your cart now…"]  # claimed before the first await
        lines = [(m.offer, m.request.qty) for m in best.assignments.values() if m.offer.platform == shop]
        try:
            self.shop_pushes[(rid, shop)] = [f"{a.label}: {x}" for x in await a.add_to_cart(lines)]
            self.pushed_lines[(rid, shop)] = _lines_key(lines)
        except BaseException as e:  # a failed or cancelled push can be tried again
            del self.shop_pushes[(rid, shop)]
            if not isinstance(e, Exception):
                raise
            return [f"{a.label}: cart push failed: {type(e).__name__}: {e}. Try again."]
        self._render(rid)  # filling a cart is not placing the order: you still tick Done
        return self.shop_pushes[(rid, shop)]

    def mark_done(self, rid: str, shop: str, done: bool = True) -> bool:
        result = self.results.get(rid)
        if result is None or not result.best or shop not in result.best.platforms:
            return False
        (self.done.setdefault(rid, set()).add if done else self.done.setdefault(rid, set()).discard)(shop)
        self.store.log("order_done" if done else "order_undone", rid, shop)
        self._render(rid)
        return True

    async def push_carts(self, result: CompareResult, skip: set[str] = frozenset()) -> list[str]:
        """Put the best plan into your real carts where the platform allows it; otherwise say how.
        ``skip``: shops already pushed from their order card."""
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
            if platform in skip:
                out.append(f"{name}: already added from its order card")
                continue
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
