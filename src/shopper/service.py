"""Search every enabled platform for every item, match, and optimise the basket."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from .adapters import Adapter, NotCalibrated
from .config import Settings
from .matching import ValueTip, best_match, value_tip
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


@dataclass
class CompareResult:
    items: list[ItemResult]
    platforms: list[str]
    proposals: list[Proposal]
    errors: dict[str, str]
    unverified_fees: list[str]
    created_at: datetime = field(default_factory=now_utc)

    @property
    def best(self) -> Proposal | None:
        return self.proposals[0] if self.proposals else None

    @property
    def single(self) -> Proposal | None:
        return best_single(self.proposals)


def search_phrase(request: ItemRequest) -> str:
    """Catalogue search works better without size tokens; matching still checks the size."""
    return strip_sizes(request.query) or request.query


async def compare(
    requests: list[ItemRequest],
    settings: Settings,
    adapters: dict[str, Adapter],
    max_orders: int | None = None,
    exact_only: bool = False,
) -> CompareResult:
    active = [adapters[p.name] for p in settings.enabled() if p.name in adapters]
    errors: dict[str, str] = {}
    limits = {a.name: asyncio.Semaphore(1 if a.needs_browser else 3) for a in active}
    results = [ItemResult(r) for r in requests]

    async def run(i: int, a: Adapter) -> None:
        async with limits[a.name]:
            try:
                offers = await a.search(search_phrase(requests[i]))
            except NotCalibrated as e:
                errors[a.name] = str(e)
                return
            except Exception as e:
                log.exception("%s search failed", a.name)
                errors[a.name] = f"{type(e).__name__}: {e}"
                return
        results[i].candidates[a.name] = offers
        results[i].matches[a.name] = best_match(requests[i], offers)

    await asyncio.gather(*(run(i, a) for i in range(len(requests)) for a in active))

    matches = {
        a.name: {i: r.matches[a.name] for i, r in enumerate(results) if r.matches.get(a.name)} for a in active
    }
    fees = settings.fees()
    proposals = optimise(matches, len(requests), fees, max_orders or settings.max_orders, exact_only)
    unverified = [a.name for a in active if not (a.name in fees and fees[a.name].verified)]
    if proposals:
        for i, r in enumerate(results):
            pool = [o for offers in r.candidates.values() for o in offers]
            r.tip = value_tip(r.request, proposals[0].assignments.get(i), pool)
    return CompareResult(results, [a.name for a in active], proposals, errors, unverified)


def all_offers(result: CompareResult) -> list[Offer]:
    return [o for r in result.items for offers in r.candidates.values() for o in offers]
