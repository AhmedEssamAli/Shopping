from shopper.matching import best_match, score
from shopper.models import ItemRequest, Offer


def offer(name, price, platform="p", in_stock=True):
    return Offer(platform=platform, name=name, price=price, url=f"https://x/{name}", in_stock=in_stock)


def test_exact_when_words_and_size_agree():
    m = score(ItemRequest("Juhayna skimmed milk 1L"), offer("Juhayna - Milk Skimmed - 1l", 54.95))
    assert m.exact and m.score >= 95


def test_variant_word_blocks_exact():
    m = score(ItemRequest("Juhayna skimmed milk 1L"), offer("Juhayna - Milk Lactose Skimmed Cream - 1l", 109.95))
    assert not m.exact


def test_wrong_size_blocks_exact():
    m = score(ItemRequest("Juhayna skimmed milk 1L"), offer("Juhayna Skimmed Milk - 200 ml", 15))
    assert not m.exact


def test_pack_is_a_different_size():
    m = score(ItemRequest("Juhayna skimmed milk 1L"), offer("Juhayna - Milk Skimmed Save 10LE - 6*1l", 320))
    assert not m.exact


def test_missing_word_is_rejected():
    m = score(ItemRequest("Juhayna full cream milk 1L"), offer("Juhayna - Milk Skimmed - 1l", 54.95))
    assert m.score < 75


def test_best_match_prefers_exact_over_cheaper_wrong_size():
    offers = [
        offer("Juhayna Skimmed Milk - 200 ml", 12.75),
        offer("Juhayna Skimmed Milk - 1 Liter", 55.45),
        offer("Juhayna Lactose Free Skimmed Milk 1L", 50),
    ]
    m = best_match(ItemRequest("Juhayna skimmed milk 1L"), offers)
    assert m.offer.price == 55.45 and m.exact


def test_best_match_skips_out_of_stock():
    offers = [offer("Juhayna Skimmed Milk - 1 Liter", 40, in_stock=False), offer("Juhayna Skimmed Milk - 1 Liter", 55)]
    assert best_match(ItemRequest("Juhayna skimmed milk 1L"), offers).offer.price == 55


def test_best_match_none_when_nothing_close():
    assert best_match(ItemRequest("Basmati rice 5kg"), [offer("Juhayna Skimmed Milk - 1 Liter", 55)]) is None


def test_accents_and_generic_extras_still_exact():
    m = score(ItemRequest("Nescafe classic 200g"), offer("Nestlé Nescafé Classic Instant Coffee - 200gm", 261.95))
    assert m.exact


def test_cheapest_exact_wins_over_higher_scoring_exact():
    offers = [offer("Nescafe Classic - 200 g", 274.95), offer("Nestle Nescafe Classic Instant Coffee 200g", 250)]
    assert best_match(ItemRequest("Nescafe classic 200g"), offers).offer.price == 250


def test_flavour_word_blocks_exact():
    assert not score(ItemRequest("Tiger chips salt"), offer("Tiger Excellence Salt & Vinegar Potato Chips - 75 g", 14.95)).exact
