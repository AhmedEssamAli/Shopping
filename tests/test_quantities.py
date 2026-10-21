"""Phase 2 of the bulk-ordering spec: quantities, removal and the order limit, without a new search."""
from fastapi.testclient import TestClient

from shopper.config import PlatformConfig, PlatformFees, Settings
from shopper.app import App
from shopper.models import ItemRequest
from shopper.web import create_app
from test_service import Fake as _Fake


class Fake(_Fake):
    calls = 0

    async def search(self, query, limit=12):
        self.calls += 1
        return await super().search(query, limit)

CAT = {"metro": [("Juhayna Skimmed Milk 1L", 52.95), ("Fresh Eggs 30 pcs", 160.0)],
       "gourmet": [("Juhayna Skimmed Milk 1L", 58.0), ("Al Doha Basmati Rice 5kg", 365.0), ("Fresh Eggs 30 pcs", 170.0)]}


def core(tmp_path):
    fees = {"metro": PlatformFees(25, verified=True), "gourmet": PlatformFees(35, free_delivery_over=500, verified=True)}
    s = Settings(home=tmp_path, platforms={n: PlatformConfig(n, n.title(), "live", True, fees[n]) for n in CAT})
    s.web_token = "k"
    app = App(s)
    app.adapters = {n: Fake(n, items) for n, items in CAT.items()}
    return app


LIST = [ItemRequest("Juhayna skimmed milk 1L", 2), ItemRequest("Al Doha basmati rice 5kg"), ItemRequest("eggs 30 pcs")]


async def test_quantity_change_replans_without_searching(tmp_path):
    app = core(tmp_path)
    result, _, rid = await app.run(LIST)
    searches = {n: a.calls for n, a in app.adapters.items()}
    before = result.best.total
    assert app.edit_line(rid, 0, "Juhayna skimmed milk 1L", qty=6) == "ok"
    assert {n: a.calls for n, a in app.adapters.items()} == searches  # no shop searched again
    milk = result.best.assignments[0]
    assert milk.request.qty == 6 and milk.line_total == 6 * milk.offer.price and result.best.total > before
    assert app.edit_line(rid, 0, "someone else's line", qty=1) == "stale"
    assert app.edit_line("nope", 0, None, qty=1) == "gone"


async def test_remove_undo_and_order_limit(tmp_path):
    app = core(tmp_path)
    result, _, rid = await app.run(LIST)
    assert app.edit_line(rid, 1, "Al Doha basmati rice 5kg", remove=True) == "ok"
    assert [r.request.query for r in result.items] == ["Juhayna skimmed milk 1L", "eggs 30 pcs"]
    assert app.undo_remove(rid) and len(result.items) == 3 and result.items[1].request.query == "Al Doha basmati rice 5kg"
    assert app.set_orders(rid, 1) and len(result.best.platforms) == 1
    assert app.set_orders(rid, 2) and not app.set_orders(rid, 9)


async def test_result_page_steppers_and_segment(tmp_path):
    app = core(tmp_path)
    client = TestClient(create_app(app))
    app.add_to_list(LIST)
    page = client.post("/list/compare?t=k", follow_redirects=True)
    rid = page.url.path.split("/")[-1]
    assert "class=stepper" in page.text and "Up to 2 shops" in page.text and "1 shop" in page.text
    r = client.post(f"/r/{rid}/qty?t=k", data={"item": 0, "line": "Juhayna skimmed milk 1L", "qty": "2", "delta": "1"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("#line-0")
    assert app.results[rid].items[0].request.qty == 3
    client.post(f"/r/{rid}/qty?t=k", data={"item": 0, "line": "Juhayna skimmed milk 1L", "qty": "x"})
    assert app.results[rid].items[0].request.qty == 3  # a bad number changes nothing
    page = client.post(f"/r/{rid}/remove?t=k", data={"item": 2, "line": "eggs 30 pcs"}, follow_redirects=True)
    assert "Line removed." in page.text and len(app.results[rid].items) == 2
    client.post(f"/r/{rid}/undo?t=k")
    assert len(app.results[rid].items) == 3
    client.post(f"/r/{rid}/orders?t=k", data={"limit": 1})
    assert app.results[rid].max_orders == 1 and "aria-pressed=true class=on><span>1 shop" in client.get(f"/r/{rid}?t=k").text
    app.results.clear()  # after a restart the page is history: no steppers, and changes say so
    hist = client.get(f"/r/{rid}?t=k").text
    assert "class=stepper" not in hist and "class=qty>" in hist
    assert client.post(f"/r/{rid}/qty?t=k", data={"item": 0, "qty": "1"}).status_code == 410


def test_household_list_steppers(tmp_path):
    app = core(tmp_path)
    app.add_to_list(LIST)
    client = TestClient(create_app(app))
    assert "class=stepper" in client.get("/?t=k").text
    client.post("/list/qty?t=k", data={"index": 0, "line": "Juhayna skimmed milk 1L", "qty": "2", "delta": "1"})
    assert app.list_items()[0].qty == 3
    client.post("/list/qty?t=k", data={"index": 0, "line": "wrong line", "qty": "9"})
    assert app.list_items()[0].qty == 3
    client.post("/list/qty?t=k", data={"index": 2, "line": "eggs 30 pcs", "qty": "1", "delta": "-1"})
    assert [i.query for i in app.list_items()] == ["Juhayna skimmed milk 1L", "Al Doha basmati rice 5kg"]
