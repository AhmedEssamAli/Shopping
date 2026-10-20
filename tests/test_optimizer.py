from shopper.models import ItemRequest, Match, Offer, PlatformFees
from shopper.optimizer import best_single, optimise


def m(platform, i, price, qty=1, exact=True):
    req = ItemRequest(f"item{i}", qty)
    return Match(req, Offer(platform=platform, name=f"item{i}", price=price, url="u"), 100, exact)


def grid(rows):
    """rows: {platform: {item_index: price}}"""
    return {p: {i: m(p, i, price) for i, price in items.items()} for p, items in rows.items()}


def test_split_wins_when_savings_beat_extra_fee():
    matches = grid({"A": {0: 100, 1: 50}, "B": {0: 60, 1: 90}})
    fees = {"A": PlatformFees(delivery_fee=10), "B": PlatformFees(delivery_fee=10)}
    best = optimise(matches, 2, fees)[0]
    assert best.platforms == ("A", "B")
    assert best.total == 60 + 50 + 20


def test_single_wins_when_extra_fee_eats_savings():
    matches = grid({"A": {0: 100, 1: 50}, "B": {0: 95, 1: 90}})
    fees = {"A": PlatformFees(delivery_fee=30), "B": PlatformFees(delivery_fee=30)}
    best = optimise(matches, 2, fees)[0]
    assert best.platforms == ("A",)
    assert best.total == 180


def test_min_order_makes_subset_infeasible():
    matches = grid({"A": {0: 100, 1: 50}, "B": {0: 40, 1: 90}})
    fees = {"A": PlatformFees(), "B": PlatformFees(min_order=200)}
    props = optimise(matches, 2, fees)
    assert all("B" not in p.platforms for p in props)


def test_free_delivery_threshold():
    fees = PlatformFees(delivery_fee=25, free_delivery_over=500)
    assert fees.fees_for(499) == 25
    assert fees.fees_for(500) == 0


def test_full_coverage_beats_cheaper_partial():
    matches = grid({"A": {0: 10}, "B": {0: 50, 1: 50}})
    best = optimise(matches, 2, {}, max_orders=1)[0]
    assert best.platforms == ("B",) and not best.missing


def test_quantity_multiplies_line_total():
    matches = {"A": {0: m("A", 0, 20, qty=3)}, "B": {0: m("B", 0, 25, qty=3)}}
    best = optimise(matches, 1, {})[0]
    assert best.total == 60


def test_exact_only_drops_close_matches():
    matches = {"A": {0: m("A", 0, 10, exact=False)}, "B": {0: m("B", 0, 30)}}
    assert optimise(matches, 1, {}, exact_only=True)[0].platforms == ("B",)


def test_best_single():
    matches = grid({"A": {0: 100, 1: 50}, "B": {0: 60, 1: 90}})
    props = optimise(matches, 2, {})
    assert best_single(props).platforms == ("A",)


def test_exact_in_subset_beats_cheaper_close_match():
    matches = {"A": {0: m("A", 0, 10, exact=False)}, "B": {0: m("B", 0, 30)}}
    best = optimise(matches, 1, {})[0]
    assert best.platforms == ("B",) and best.close_count == 0


def test_close_match_used_when_no_exact_exists():
    matches = {"A": {0: m("A", 0, 10, exact=False)}, "B": {0: m("B", 0, 30, exact=False)}}
    assert optimise(matches, 1, {})[0].platforms == ("A",)


def test_moves_an_item_to_meet_a_minimum_order():
    def m(p, i, price):
        return Match(ItemRequest(f"item {i}"), Offer(p, f"x{i}", price, "u"), 100, True)
    matches = {"A": {0: m("A", 0, 90), 1: m("A", 1, 20)}, "B": {0: m("B", 0, 89), 1: m("B", 1, 25), 2: m("B", 2, 50)}}
    fees = {"A": PlatformFees(min_order=100), "B": PlatformFees()}
    best = optimise(matches, 3, fees)[0]
    assert best.platforms == ("A", "B") and round(best.total, 2) == 160.0
    assert best.assignments[0].offer.platform == "A"


def test_minimum_move_never_starves_the_other_shop():
    def m(p, i, price):
        return Match(ItemRequest(f"item {i}"), Offer(p, f"x{i}", price, "u"), 100, True)
    matches = {"A": {0: m("A", 0, 100), 1: m("A", 1, 10)}, "B": {0: m("B", 0, 101), 1: m("B", 1, 11), 2: m("B", 2, 89)}}
    fees = {"A": PlatformFees(min_order=100), "B": PlatformFees(min_order=100)}
    best = optimise(matches, 3, fees)[0]
    assert best.platforms == ("A", "B") and round(best.total, 2) == 200.0


def test_service_fee_floor_and_cap_and_free_delivery():
    from shopper.config import REPO_ROOT, load_platforms
    f = load_platforms(REPO_ROOT / "config" / "platforms.toml")["amazon_now"].fees
    assert f.verified
    assert f.fees_for(56.69) == 25 + 5          # 4% is 2.27: the EGP 5 floor
    assert round(f.fees_for(200), 2) == 8.0     # free delivery from 150; 4% of 200
    assert f.fees_for(400) == 10                # capped at EGP 10


def test_small_order_fee():
    from shopper.config import REPO_ROOT, load_platforms
    from shopper.models import PlatformFees
    f = load_platforms(REPO_ROOT / "config" / "platforms.toml")["noon_minutes"].fees
    assert f.fees_for(86.75) == 30  # your checkout: delivery 25 + small order fee 5
    known = PlatformFees(delivery_fee=25, small_order_fee=5, small_order_below=100)
    assert known.fees_for(86.75) == 30 and known.fees_for(120) == 25
