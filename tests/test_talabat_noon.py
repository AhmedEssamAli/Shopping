import json
from datetime import timedelta
from pathlib import Path

from shopper.adapters.noon import NoonAdapter, _hits, relevance
from shopper.adapters.talabat import TalabatShopAdapter
from shopper.matching import best_match
from shopper.models import ItemRequest, Offer, now_utc

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def talabat(tmp_path):
    return TalabatShopAdapter("talabat_793312", "talabat Mart", 8192, 793312, "talabat-mart-tagammoa-5-south-investors", tmp_path)


def test_talabat_menu_and_items_parse():
    store, subs = TalabatShopAdapter.parse_menu(load("talabat_menu.json")["pageProps"])
    assert store["minimumOrderValue"] == 40 and ("milk", "long-life-milk", 23) in subs
    items, pages = TalabatShopAdapter.parse_items(load("talabat_items_long_life_milk.json")["pageProps"])
    assert pages == 1 and len(items) == 20 and items[1]["title"] == "Almarai Plain Milk Full Fat Bottle 1L"


def test_talabat_redirect_page_is_empty():
    assert TalabatShopAdapter.parse_items({"__N_REDIRECT": "/x", "__N_REDIRECT_STATUS": 307}) == ([], 0)


def snap(items, age=timedelta(0)):
    store = {"deliveryFee": 0, "minimumOrderValue": 40}
    return {"fetched_at": (now_utc() - age).isoformat(), "store": store, "items": items}


async def test_talabat_search_from_snapshot(tmp_path):
    a = talabat(tmp_path)
    items, _ = TalabatShopAdapter.parse_items(load("talabat_items_long_life_milk.json")["pageProps"])
    a.cache_path.write_text(json.dumps(snap(items)), encoding="utf-8")
    offers = await a.search("almarai full fat milk")
    m = best_match(ItemRequest("Almarai full fat milk 1L"), offers)
    assert m.exact and m.offer.price == 55
    assert m.offer.url == ("https://www.talabat.com/egypt/grocery/793312/talabat-mart-tagammoa-5-south-investors"
                           f"/product/{items[1]['slug']}/s/{items[1]['sku']}?aid=8192")
    fees = a.live_fees()
    assert fees.verified and fees.min_order == 40 and fees.delivery_fee == 0


async def test_talabat_stale_snapshot_served_while_refreshing(tmp_path):
    a = talabat(tmp_path)
    a.cache_path.write_text(json.dumps(snap([{"title": "Old Milk 1L", "slug": "old", "price": 10, "originalPrice": 10,
                                               "sku": "1", "stockAmount": 5}], age=timedelta(days=2))))
    calls = []

    async def fake_refresh():
        calls.append(1)
        return snap([{"title": "New Milk 1L", "slug": "new", "price": 9, "originalPrice": 9, "sku": "2", "stockAmount": 5}])

    a.refresh = fake_refresh
    first = await a.search("milk")
    assert first[0].name == "Old Milk 1L"  # stale copy answers at once
    await a._bg
    assert calls == [1] and (await a.search("milk"))[0].name == "New Milk 1L"


def test_noon_parse_capture_hits():
    hits = load("noon_hits_sample.json")["hits"]
    offers = NoonAdapter(session=None).parse(hits)
    o = offers[0]
    assert o.price == 1050 and o.regular_price == 1499 and o.cart_ref == hits[0]["offer_code"]
    assert o.url.startswith("https://www.noon.com/egypt-en/") and o.url.endswith(f"/p/?o={hits[0]['offer_code']}")
    assert offers[2].regular_price is None  # no sale price -> no strike-through


def test_noon_relevance_rejects_unrelated_feeds():
    hits = load("noon_hits_sample.json")["hits"]
    assert relevance(hits, "nescafe classic") == 0
    assert relevance(hits, "earbuds") > 0
    assert _hits({"hits": [{"offer_code": "x"}]}) and _hits({"hits": []}) is None and _hits([1]) is None


async def test_push_carts_reports_per_platform(tmp_path):
    from shopper.app import App
    from test_service import ADAPTERS, settings

    class Pushy(type(ADAPTERS["a"])):
        supports_cart_push = True

        async def add_to_cart(self, lines):
            return [f"added: {q} × {o.name}" for o, q in lines]

    core = App(settings(tmp_path))
    core.adapters = dict(ADAPTERS, b=Pushy("b", ADAPTERS["b"].catalogue))
    result, _, _ = await core.run([ItemRequest("Juhayna full cream milk 1L", 2), ItemRequest("Egyptian rice 5kg")])
    lines = await core.push_carts(result)
    assert any(l.startswith("B: added: 2 ×") for l in lines)
    assert any(l.startswith("A: no cart push") for l in lines)


async def test_talabat_backs_off_on_429_then_succeeds(tmp_path, monkeypatch):
    import httpx
    import shopper.adapters.talabat as tal

    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"pageProps": {"ok": True}})

    a = talabat(tmp_path)
    a._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await a._get("menu.json") == {"ok": True} and len(calls) == 3
    await a.close()


async def test_failed_aisles_keep_old_prices_and_retry(tmp_path):
    a = talabat(tmp_path)
    old_item = {"title": "Old Rice 1kg", "slug": "r", "price": 50, "originalPrice": 50, "sku": "9", "stockAmount": 3}
    a.cache_path.write_text(json.dumps(snap([old_item])), encoding="utf-8")
    menu = {"initialState": {"groceryStore": {"deliveryFee": 0}, "categories": [
        {"slug": "milk", "subCategories": [{"slug": "ok", "count": 1}]},
        {"slug": "rice", "subCategories": [{"slug": "broken", "count": 1}]}]}}

    async def fake_get(route, **kw):
        if route == "menu.json":
            return menu
        if kw["subCategorySlug"] == "broken":
            raise RuntimeError("429")
        return {"initialState": {"itemsData": {"pageCount": 1, "items": [
            {"title": "New Milk 1L", "slug": "m", "price": 40, "originalPrice": 40, "sku": "1", "stockAmount": 2}]}}}

    a._get = fake_get
    s = await a.refresh()
    assert s["failed"] == 1 and {i["sku"] for i in s["items"]} == {"1", "9"}
    assert not a._is_fresh(s)  # incomplete -> retried later


async def test_first_download_never_blocks_a_search(tmp_path):
    import asyncio

    import pytest
    a = talabat(tmp_path)
    gate = asyncio.Event()

    async def slow_refresh():
        await gate.wait()
        return snap([{"title": "Milk 1L", "slug": "m", "price": 9, "originalPrice": 9, "sku": "2", "stockAmount": 5}])

    a.refresh = slow_refresh
    warm = asyncio.create_task(a.snapshot(wait=True))  # serve's start-up warm-up
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="still downloading"):
        await asyncio.wait_for(a.search("milk"), 1)  # answers at once instead of waiting minutes
    gate.set()
    assert (await warm)["items"][0]["title"] == "Milk 1L"
    assert [o.name for o in await a.search("milk")] == ["Milk 1L"]
