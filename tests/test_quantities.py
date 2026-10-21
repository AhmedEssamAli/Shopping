"""Phase 2 of the bulk-ordering spec: quantities, removal and the order limit, without a new search."""
from fastapi.testclient import TestClient

from shopper.config import PlatformConfig, PlatformFees, Settings
from shopper.app import App
from shopper.models import ItemRequest
from shopper.web import create_app
from test_service import Fake as _Fake, rows


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
    page = client.post("/compare?t=k", data=rows(app.list_items()), follow_redirects=True)
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


def test_pasted_list_goes_to_the_review_step(tmp_path):
    app = core(tmp_path)
    client = TestClient(create_app(app))
    page = client.post("/compare?t=k", data={"items": "4 Juhayna skimmed milk 1L\nAl Doha basmati rice 5kg\neggs 30 pcs"})
    assert page.status_code == 200 and "Check your list" in page.text and "Nothing searched yet" in page.text
    assert not app.results  # no shop searched yet
    assert page.text.count("class=itemrow") == 3 and "value='Juhayna'" in page.text and "value='skimmed'" in page.text
    assert "<option selected>L</option>" in page.text and "value='30'" in page.text and "value='4'" in page.text
    assert "Compare <span id=count>3</span> items" in page.text and "aria-current=step><b>2</b>" in page.text
    # The rows as shown (after edits) are what gets compared, or saved as the list.
    data = {"item": ["milk", "rice"], "brand": ["Juhayna", "Al Doha"], "kind": ["skimmed", "basmati"], "amount": ["1", "5"],
            "unit": ["L", "kg"], "pieces": ["", ""], "qty": ["5", "1"]}
    client.post("/list/save?t=k", data=data)
    assert [(i.qty, i.query) for i in app.list_items()] == [(5, "Juhayna skimmed milk 1L"), (1, "Al Doha basmati rice 5kg")]
    done = client.post("/compare?t=k", data=data, follow_redirects=True)
    rid = done.url.path.split("/")[-1]
    assert [(r.request.qty, r.request.query) for r in app.results[rid].items] == [(5, "Juhayna skimmed milk 1L"),
                                                                                  (1, "Al Doha basmati rice 5kg")]
    assert "Check your list" in client.post("/list/compare?t=k").text


async def test_order_cards_copy_tick_and_progress(tmp_path):
    app = core(tmp_path)
    result, _, rid = await app.run(LIST, max_orders=2)
    client = TestClient(create_app(app))
    page = client.get(f"/r/{rid}?t=k").text
    n = len(result.best.platforms)
    assert f"Order 1 of {n}" in page and "Copy list and open" in page and "data-text='2 × Juhayna Skimmed Milk 1L" in page
    if n > 1:
        assert f"<b>0 of {n}</b> orders placed" in page
    shop = result.best.platforms[0]
    page = client.post(f"/r/{rid}/done?t=k", data={"shop": shop, "done": 1}, follow_redirects=True).text
    assert "Placed" in page and "Mark as not placed" in page and "aria-current=step><b>4</b>" in page
    client.post(f"/r/{rid}/done?t=k", data={"shop": shop, "done": 0})
    assert "Mark as not placed" not in client.get(f"/r/{rid}?t=k").text
    app.results.clear()
    assert client.post(f"/r/{rid}/done?t=k", data={"shop": shop}).status_code == 410


def test_saved_lists_and_buy_again(tmp_path):
    app = core(tmp_path)
    app.add_to_list(LIST)
    client = TestClient(create_app(app))
    client.post("/lists/save?t=k", data={"name": " Weekly "})
    assert app.named_lists() == [("Weekly", 3)]
    home = client.get("/?t=k").text
    assert "Saved lists" in home and "Review and compare" in home and "Make it my list" in home
    review = client.post("/lists/review?t=k", data={"name": "Weekly"}).text
    assert "Check your list" in review and review.count("class=itemrow") == 3
    app.clear_list()
    client.post("/lists/use?t=k", data={"name": "Weekly"})
    assert [i.qty for i in app.list_items()] == [2, 1, 1]
    assert client.post("/lists/save?t=k", data={"name": ""}).status_code == 400
    done = client.post("/compare?t=k", data=rows(app.list_items()), follow_redirects=True)
    rid = done.url.path.split("/")[-1]
    assert "Buy again" in client.get("/?t=k").text
    again = client.post(f"/r/{rid}/reorder?t=k").text
    assert "Check your list" in again and "value='2'" in again
    client.post("/lists/delete?t=k", data={"name": "Weekly"})
    assert app.named_lists() == []
    kinds = [k for _, k, _, _ in app.store.events()]
    assert "review" in kinds and "compare" in kinds


async def test_telegram_order_messages_and_lists(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from telegram.ext import CallbackQueryHandler, CommandHandler
    from shopper.bot import build_bot, order_messages
    app = core(tmp_path)
    result, _, rid = await app.run(LIST, max_orders=2)
    msgs = order_messages(app, result, rid)
    assert msgs and msgs[0][0].startswith(f"Order 1 of {len(result.best.platforms)} · ")
    shop = sorted(result.best.platforms, key=lambda p: -result.best.subtotals[p])[0]
    done = next(b for b in msgs[0][1].inline_keyboard[0] if b.text == "Done")
    app.settings.telegram_token, app.settings.allowed_chat_ids = "123456:TEST", {42}
    hs = build_bot(app).handlers[0]
    button = next(h for h in hs if isinstance(h, CallbackQueryHandler))
    query = SimpleNamespace(data=done.callback_data, answer=AsyncMock(), edit_message_text=AsyncMock())
    chat = SimpleNamespace(id=42, send_action=AsyncMock())
    await button.callback(SimpleNamespace(callback_query=query, effective_chat=chat,
                                          effective_message=SimpleNamespace(reply_text=AsyncMock())), None)
    assert shop in app.done[rid] and "✓ placed" in query.edit_message_text.call_args.args[0]
    app.add_to_list(LIST)
    app.save_named("Weekly")
    lists = next(h for h in hs if isinstance(h, CommandHandler) and "lists" in h.commands)
    sent = AsyncMock()
    await lists.callback(SimpleNamespace(effective_chat=chat, effective_message=SimpleNamespace(text="/lists", reply_text=sent)), None)
    assert "Weekly — 3 lines" in sent.call_args.args[0]


def test_measures_from_the_event_log():
    from datetime import timedelta
    from shopper.models import now_utc
    from shopper.stats import measures
    t0 = now_utc()
    at = lambda s: (t0 + timedelta(seconds=s)).isoformat()  # noqa: E731
    ev = [(at(0), "review", "", "20"), (at(95), "compare", "r1", "20:2"), (at(120), "qty", "r1", "milk"),
          (at(200), "order_done", "r1", "metro"), (at(260), "order_done", "r1", "gourmet"),
          (at(300), "compare", "r2", "3:2"), (at(310), "order_done", "r2", "metro")]
    m = measures(ev)
    assert m["comparisons"] == 2 and m["review_to_compare_s"] == 95 and m["qty_changes"] == 1
    assert (m["split_plans"], m["split_plans_all_done"], m["all_done_pct"]) == (2, 1, 50)
