"""Choose where to buy each item so the landed cost (items + fees) is lowest."""
from __future__ import annotations

from itertools import combinations

from .models import Match, PlatformFees, Proposal


def _evaluate(
    platforms: tuple[str, ...],
    matches: dict[str, dict[int, Match]],
    n_items: int,
    fees: dict[str, PlatformFees],
    exact_only: bool,
) -> Proposal | None:
    assignments: dict[int, Match] = {}
    missing: list[int] = []
    for i in range(n_items):
        options = [matches[p][i] for p in platforms if i in matches.get(p, {})]
        exact = [m for m in options if m.exact]
        # An exact match anywhere in the subset beats any cheaper close match.
        if exact or exact_only:
            options = exact
        if options:
            assignments[i] = min(options, key=lambda m: m.line_total)
        else:
            missing.append(i)

    _meet_minimums(platforms, matches, assignments, fees)
    subtotals: dict[str, float] = {}
    for m in assignments.values():
        subtotals[m.offer.platform] = subtotals.get(m.offer.platform, 0.0) + m.line_total
    if set(subtotals) != set(platforms):
        return None  # a platform carries nothing; the smaller subset covers this case
    for p, sub in subtotals.items():
        if sub < fees.get(p, PlatformFees()).min_order:
            return None
    fee_map = {p: fees.get(p, PlatformFees()).fees_for(sub) for p, sub in subtotals.items()}
    return Proposal(platforms, assignments, subtotals, fee_map, missing)


def _meet_minimums(platforms: tuple[str, ...], matches: dict[str, dict[int, Match]],
                   assignments: dict[int, Match], fees: dict[str, PlatformFees]) -> None:
    """Move items into a shop that is under its minimum order, cheapest extra cost first.

    Each item first goes to its cheapest shop; that can leave one shop just under its
    minimum when moving a single item there would cost a pound more and save a whole order.
    A move never swaps an exact match for a close one.
    """
    def subtotal(p: str) -> float:
        return sum(m.line_total for m in assignments.values() if m.offer.platform == p)

    for p in platforms:
        need = fees.get(p, PlatformFees()).min_order
        while 0 < need and subtotal(p) < need:
            moves = [(matches[p][i].line_total - m.line_total, i) for i, m in assignments.items()
                     if m.offer.platform != p and i in matches.get(p, {}) and (matches[p][i].exact or not m.exact)
                     and subtotal(m.offer.platform) - m.line_total >= fees.get(m.offer.platform, PlatformFees()).min_order]
            if not moves:
                break
            _, i = min(moves)
            assignments[i] = matches[p][i]


def optimise(
    matches: dict[str, dict[int, Match]],
    n_items: int,
    fees: dict[str, PlatformFees],
    max_orders: int = 2,
    exact_only: bool = False,
) -> list[Proposal]:
    """Return feasible proposals, best first.

    Ranking: fewest missing items, then fewest close (non-exact) matches, then lowest
    total, then fewest orders. Accuracy comes before price: a cheaper basket of the
    wrong sizes is not a saving.
    ``matches[platform][item_index]`` holds that platform's best match for the item.
    """
    platforms = sorted(p for p, m in matches.items() if m)
    proposals = []
    for k in range(1, min(max_orders, len(platforms)) + 1):
        for subset in combinations(platforms, k):
            prop = _evaluate(subset, matches, n_items, fees, exact_only)
            if prop:
                proposals.append(prop)
    proposals.sort(key=lambda p: (len(p.missing), p.close_count, round(p.total, 2), len(p.platforms)))
    return proposals


def best_single(proposals: list[Proposal]) -> Proposal | None:
    return next((p for p in proposals if len(p.platforms) == 1), None)
