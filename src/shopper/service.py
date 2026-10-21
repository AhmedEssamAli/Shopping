"""Search every enabled platform for every item, match, and optimise the basket."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from .adapters import Adapter, NotCalibrated
from .config import Settings
from .matching import ValueTip, best_match, value_tip
from .memory import Memory, ProductCard, Recall, offer_id, product_cards
from .models import ItemRequest, Match, Offer, Proposal, now_utc
from .optimizer import best_single, optimise
from .units import strip_sizes

log = logging.getLogger(__name__)


@dataclass
class ItemResult:
    request: ItemRequest
    matches: dict[str, Match | None] = field(default_factory=dict)  # platform -> best match
    candidates: dict[str, list[Offer]] = field(default_factory=dict)  # raw offers, for the table
    tip: ValueTip | None = None  # a cheaper-per-unit pack of the item chosen in the best plan
    cards: list[ProductCard] = field(default_factory=list)  # distinct products found, for you to pick
    recall: Recall | None = None  # what was remembered for this line
    confidence: float | None = None  # the learner's probability that the plan's offer is the right product

    @property
    def pinned(self) -> bool:
        return bool(self.recall and self.recall.pins)

    @property
    def needs_pick(self) -> bool:
        """More than one distinct product fits the line and you have not chosen yet."""
        # Brands alone do not ask: an empty brand field means any brand, cheapest wins.
        return not self.pinned and len({c.group for c in self.cards if c.exact}) > 1


@dataclass
class CompareResult:
    items: list[ItemResult]
    platforms: list[str]
    proposals: list[Proposal]
    errors: dict[str, str]
    unverified_fees: list[str]
    created_at: datetime = field(default_factory=now_utc)
    max_orders: int | None = None
    exact_only: bool = False

    @property
    def best(self) -> Proposal | None:
        return self.proposals[0] if self.proposals else None

    @property
    def single(self) -> Proposal | None:
        return best_single(self.proposals)


def search_phrase(request: ItemRequest, recall: Recall | None = None) -> str:
    """Catalogue search works better without size tokens; matching still checks the size.
    Once you picked a product, its pack words go into the search too ("... pouch")."""
    text = recall.canonical if recall and recall.canonical else request.query
    return strip_sizes(text) or text


def match_on(request: ItemRequest, platform: str, offers: list[Offer], recall: Recall | None, learner=None) -> Match | None:
    """Best offer on one platform, using what you chose and rejected before, and (once
    trained) dropping offers the learner rates as the wrong product."""
    keep = learner.keep(request, offers, recall.avoid if recall else frozenset()) if learner else None
    if not recall:
        return best_match(request, offers, keep=keep)
    offers = [o for o in offers if (o.platform, offer_id(o)) not in recall.rejected]
    pin = recall.pins.get(platform)
    hit = next((o for o in offers if pin and offer_id(o) == pin and o.in_stock), None)
    if hit:
        return Match(request, hit, 100.0, True, pinned=True)
    target = ItemRequest(recall.canonical, request.qty) if recall.canonical else request
    m = best_match(target, offers, recall.avoid, keep)
    return Match(request, m.offer, m.score, m.exact) if m else None


async def compare(
    requests: list[ItemRequest],
    settings: Settings,
    adapters: dict[str, Adapter],
    max_orders: int | None = None,
    exact_only: bool = False,
    memory: Memory | None = None,
    learner=None,
) -> CompareResult:
    active = [adapters[p.name] for p in settings.enabled() if p.name in adapters]
    errors: dict[str, str] = {}
    limits = {a.name: asyncio.Semaphore(1 if a.needs_browser else 3) for a in active}
    results = [ItemResult(r, recall=memory.recall(r) if memory else None) for r in requests]

    async def run(i: int, a: Adapter) -> None:
        async with limits[a.name]:
            try:
                offers = await a.search(search_phrase(requests[i], results[i].recall))
            except NotCalibrated as e:
                errors[a.name] = str(e)
                return
            except Exception as e:
                log.exception("%s search failed", a.name)
                errors[a.name] = f"{type(e).__name__}: {e}"
                return
        results[i].candidates[a.name] = offers

    await asyncio.gather(*(run(i, a) for i in range(len(requests)) for a in active))
    result = CompareResult(results, [a.name for a in active], [], errors, [], max_orders=max_orders, exact_only=exact_only)
    return assemble(result, settings, adapters, learner=learner)


async def search_line(request: ItemRequest, settings: Settings, adapters: dict[str, Adapter], platforms: list[str],
                      memory: Memory | None = None) -> tuple[ItemResult, dict[str, str]]:
    """Search every given shop for one line (the "Search again" of a line). Returns the line and errors."""
    r = ItemResult(request, recall=memory.recall(request) if memory else None)
    errors: dict[str, str] = {}
    active = [adapters[p] for p in platforms if p in adapters]
    limits = {a.name: asyncio.Semaphore(1) for a in active}

    async def run(a: Adapter) -> None:
        async with limits[a.name]:
            try:
                r.candidates[a.name] = await a.search(search_phrase(request, r.recall))
            except NotCalibrated as e:
                errors[a.name] = str(e)
            except Exception as e:
                log.exception("%s search failed", a.name)
                errors[a.name] = f"{type(e).__name__}: {e}"

    await asyncio.gather(*(run(a) for a in active))
    return r, errors


def assemble(result: CompareResult, settings: Settings, adapters: dict[str, Adapter],
             memory: Memory | None = None, learner=None) -> CompareResult:
    """Match, group and optimise the offers already fetched. Runs again, without searching,
    after you pick or reject a product (pass ``memory`` to re-read what you chose)."""
    active = [adapters[p] for p in result.platforms if p in adapters]
    for r in result.items:
        if memory:
            r.recall = memory.recall(r.request)
        r.matches = {p: match_on(r.request, p, offers, r.recall, learner) for p, offers in r.candidates.items()}
        rejected = r.recall.rejected if r.recall else set()
        r.cards = product_cards(r.request, [o for offers in r.candidates.values() for o in offers],
                                r.recall.avoid if r.recall else frozenset(), rejected)
    matches, fees = _matches(result, active), _fees(result, settings, active)
    result.proposals = optimise(matches, len(result.items), fees, result.max_orders or settings.max_orders,
                                result.exact_only)
    result.unverified_fees = [a.name for a in active if not (a.name in fees and fees[a.name].verified)]
    for i, r in enumerate(result.items):
        rejected = r.recall.rejected if r.recall else set()
        pool = [o for offers in r.candidates.values() for o in offers if (o.platform, offer_id(o)) not in rejected]
        r.tip = value_tip(r.request, result.proposals[0].assignments.get(i), pool) if result.proposals else None
        chosen = result.proposals[0].assignments.get(i) if result.proposals else None
        r.confidence = (1.0 if chosen.pinned else learner.confidence(r.request, chosen.offer, pool)) if (chosen and learner) else None
    return result


def _matches(result: CompareResult, active: list[Adapter]) -> dict[str, dict[int, Match]]:
    return {a.name: {i: r.matches[a.name] for i, r in enumerate(result.items) if r.matches.get(a.name)} for a in active}


def _fees(result: CompareResult, settings: Settings, active: list[Adapter]) -> dict:
    fees = settings.fees()
    for a in active:  # a platform's own numbers beat what is typed in platforms.toml
        live = a.live_fees()
        if live:
            fees[a.name] = live
    return fees


def plan_options(result: CompareResult, settings: Settings, adapters: dict[str, Adapter],
                 limits: tuple[int, ...] = (1, 2, 3)) -> dict[int, Proposal | None]:
    """Best total for each order limit ("1 shop", "up to 2", "up to 3"), from the matches already made."""
    active = [adapters[p] for p in result.platforms if p in adapters]
    matches, fees = _matches(result, active), _fees(result, settings, active)
    out = {}
    for k in limits:
        best = optimise(matches, len(result.items), fees, k, result.exact_only)
        out[k] = best[0] if best else None
    return out


def all_offers(result: CompareResult) -> list[Offer]:
    return [o for r in result.items for offers in r.candidates.values() for o in offers]
