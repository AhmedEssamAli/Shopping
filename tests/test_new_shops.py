import json
from pathlib import Path

from shopper.adapters.magento import hyperone
from shopper.adapters.metro import MetroAdapter
from shopper.matching import best_match
from shopper.models import ItemRequest

FIX = Path(__file__).parent / "fixtures"


def test_metro_reads_real_search_cards():
    offers = MetroAdapter().parse((FIX / "metro_search_nescafe.html").read_text(encoding="utf-8"))
    assert len(offers) == 12
    gold = next(o for o in offers if o.name == "Nescafe Gold Instant Coffee - 190g")
    assert gold.price == 475.0 and gold.regular_price == 509.95 and gold.on_deal
    assert gold.url == "https://www.metro-markets.com/product/Nescafe-Gold-Instant-Coffee---190g/5485" and gold.sku == "5485"
    m = best_match(ItemRequest("Nescafe classic 47.5g"), offers)
    assert m.exact and m.offer.price == 89.95


def test_hyperone_uses_magento_and_product_urls():
    offers = hyperone().parse(json.loads((FIX / "hyperone_juhayna_milk.json").read_text()))
    milk = next(o for o in offers if o.name == "Juhayna Full Cream Milk - 1L")
    assert milk.price == 50.95 and milk.url == "https://www.hyperone.com.eg/en/product/1-6223000350065"


def test_new_shops_configured():
    from shopper.config import REPO_ROOT, load_platforms
    plats = load_platforms(REPO_ROOT / "config" / "platforms.toml")
    assert plats["metro_markets"].enabled and plats["metro_markets"].status == "live"
    assert not plats["hyperone"].enabled  # no New Cairo branch found yet
