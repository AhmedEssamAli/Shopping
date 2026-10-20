"""Telegram buttons and the home page."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from telegram.ext import CallbackQueryHandler, MessageHandler

from shopper.bot import MY_LIST, build_bot, chunks, list_keyboard, result_keyboard
from shopper.models import ItemRequest
from shopper.web import create_app
from test_memory import COFFEE, core


def fake_update(text=None, data=None):
    sent = AsyncMock()
    query = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock()) if data else None
    return SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=42, send_action=AsyncMock()),
                           effective_message=SimpleNamespace(reply_text=sent, text=text)), sent


def handlers(app):
    app.settings.telegram_token, app.settings.allowed_chat_ids = "123456:TEST", {42}
    hs = build_bot(app).handlers[0]
    return (next(h for h in hs if isinstance(h, CallbackQueryHandler)),
            next(h for h in hs if isinstance(h, MessageHandler)))


def test_chunks_split_at_line_breaks():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
    parts = chunks(text, 1000)
    assert all(len(p) <= 1000 for p in parts) and "\n".join(parts) == text
    assert all(p.split("\n")[0].startswith("line ") for p in parts)


async def test_result_buttons(tmp_path):
    app = core(tmp_path)
    result, _, rid = await app.run([COFFEE])
    rows = [b for row in result_keyboard(app, result, rid).inline_keyboard for b in row]
    assert [b.text for b in rows] == ["Save as my list"]  # no push shop in plan; 127.0.0.1 is no link
    app.settings.web_base_url = "https://pc.tail.ts.net"
    app.adapters["amazon"].supports_cart_push = True
    rows = [b for row in result_keyboard(app, result, rid).inline_keyboard for b in row]
    assert "push" not in rows[0].callback_data  # not final: no push yet
    app.feedback(rid, "pick", 0, card=next(c.key for c in result.items[0].cards if "pouch" in c.forms))
    rows = [b for row in result_keyboard(app, result, rid).inline_keyboard for b in row]
    assert rows[0].callback_data == f"push:{rid}" and rows[-1].url.startswith("https://pc.tail.ts.net/r/")


def test_list_keyboard_rows():
    kb = list_keyboard([ItemRequest(f"item {i}") for i in range(6)]).inline_keyboard
    assert [b.text for b in kb[0]] == ["Remove 1", "Remove 2", "Remove 3", "Remove 4"]
    assert kb[0][0].callback_data.startswith("rm:0:")
    assert [b.callback_data for b in kb[-1]] == ["cmp", "clr"] and list_keyboard([]).inline_keyboard == ()


async def test_menu_and_list_buttons(tmp_path):
    app = core(tmp_path)
    on_button, on_text = handlers(app)
    result, _, rid = await app.run([COFFEE, ItemRequest("rice 5kg", 2)])
    update, sent = fake_update(data=f"save:{rid}")
    await on_button.callback(update, None)
    assert [i.query for i in app.list_items()] == ["Nescafe classic 200g", "rice 5kg"]

    update, sent = fake_update(text=MY_LIST)
    await on_text.callback(update, None)
    assert "2. 2 × rice 5kg" in sent.call_args.args[0]

    from shopper.bot import line_tag
    update, _ = fake_update(data=f"rm:0:{line_tag('Nescafe classic 200g')}")
    await on_button.callback(update, None)
    assert [i.query for i in app.list_items()] == ["rice 5kg"]
    await on_button.callback(update, None)  # the same stale button again removes nothing
    assert [i.query for i in app.list_items()] == ["rice 5kg"]
    assert "1. 2 × rice 5kg" in update.callback_query.edit_message_text.call_args.args[0]

    update, _ = fake_update(data="clr")
    await on_button.callback(update, None)
    assert app.list_items() == []


async def test_buttons_ignore_other_chats(tmp_path):
    app = core(tmp_path)
    on_button, _ = handlers(app)
    app.add_to_list([COFFEE])
    update, _ = fake_update(data="clr")
    update.effective_chat.id = 7
    await on_button.callback(update, None)
    assert len(app.list_items()) == 1


def test_home_page_list_chips_recent_and_shops(tmp_path):
    app = core(tmp_path)
    client = TestClient(create_app(app))
    assert "The household list" in client.get("/?t=k").text
    client.post("/list/add?t=k", data={"items": "Nescafe classic 200g\n2 rice 5kg"})
    home = client.get("/?t=k").text
    assert "Nescafe classic 200g" in home and "Compare the list" in home and "Through Shopping Chrome" not in home
    page = client.post("/list/compare?t=k", follow_redirects=True)
    rid = page.url.path.split("/")[-1]
    client.post(f"/r/{rid}/pick?t=k", data={"item": 0, "card": app.results[rid].items[0].cards[0].key})
    home = client.get("/?t=k").text
    assert "Products you chose before" in home and "data-line='Nescafe classic 200g'" in home
    assert "Recent comparisons" in home and f"/r/{rid}?t=k" in home and "2 items: Nescafe classic 200g, rice 5kg" in home
    assert "<b>Live</b>" in home
    client.post("/list/remove?t=k", data={"index": 0})
    assert [i.query for i in app.list_items()] == ["rice 5kg"]
    assert client.post("/list/clear").status_code == 404  # token required
    assert client.post("/list/wipe?t=k").status_code == 404


async def test_web_push_runs_once_and_history_pages_lose_buttons(tmp_path):
    app = core(tmp_path)
    calls = []

    async def push(result):
        calls.append(1)
        return ["Amazon: added: 1 × pouch"]
    app.push_carts = push
    app.adapters["amazon"].supports_cart_push = True
    client = TestClient(create_app(app))
    page = client.post("/compare?t=k", data={"items": "Nescafe classic 200g", "max_orders": 1}, follow_redirects=True)
    rid = page.url.path.split("/")[-1]
    assert "Add the best plan to my carts" not in page.text  # not final yet
    app.feedback(rid, "pick", 0, card=next(c.key for c in app.results[rid].items[0].cards if "pouch" in c.forms))
    assert "Add the best plan to my carts (AMAZON)" in client.get(f"/r/{rid}?t=k").text
    first = client.post(f"/r/{rid}/push?t=k", follow_redirects=True)
    client.post(f"/r/{rid}/push?t=k")
    assert len(calls) == 1 and "added: 1 × pouch" in first.text
    assert "Added to your carts already" in client.get(f"/r/{rid}?t=k").text

    app.results.clear()  # as after a restart
    old = client.get(f"/r/{rid}?t=k").text
    assert "From history" in old and "This one" not in old and "not this" not in old
    gone = client.post(f"/r/{rid}/pick?t=k", data={"item": 0, "card": "x"})
    assert gone.status_code == 410 and "Compare the list again" in gone.text and "<html" in gone.text


def test_friendly_404_and_saved_web_token(tmp_path):
    from shopper.cli import ensure_web_token
    client = TestClient(create_app(core(tmp_path)))
    bad = client.get("/?t=wrong")
    assert bad.status_code == 404 and "Page not found" in bad.text and "access key" in bad.text
    first = ensure_web_token(tmp_path / "h")
    assert len(first) >= 20 and ensure_web_token(tmp_path / "h") == first


def test_household_list_merge_keeps_order(tmp_path):
    from shopper.store import Store
    st = Store(tmp_path / "db.sqlite3")
    st.add_to_list(42, [ItemRequest("milk"), ItemRequest("rice")])
    st.add_to_list(7, [ItemRequest("eggs")])
    st.merge_lists_into(0)
    assert [i.query for i in st.get_list(0)] == ["eggs", "milk", "rice"] or [i.query for i in st.get_list(0)] == ["milk", "rice", "eggs"]
    assert [i.query for i in st.get_list(0)][1:] in (["milk", "rice"], ["rice", "eggs"])


async def test_add_without_text_goes_step_by_step(tmp_path):
    from telegram.ext import CommandHandler

    from shopper.bot import ASK, MY_LIST
    from shopper.models import Offer
    app = core(tmp_path)
    app.store.record_offers([Offer("a", "Juhayna Skimmed Milk 1L", 56, "u1"), Offer("a", "Almarai Full Cream Milk 1L", 55, "u2")])
    app.settings.telegram_token, app.settings.allowed_chat_ids = "123456:TEST", {42}
    hs = build_bot(app).handlers[0]
    add = next(h for h in hs if isinstance(h, CommandHandler) and "add" in h.commands)
    button = next(h for h in hs if isinstance(h, CallbackQueryHandler))
    on_text = next(h for h in hs if isinstance(h, MessageHandler))
    update, sent = fake_update(text="/add")
    await add.callback(update, None)
    assert sent.call_args.args[0] == ASK["item"]

    update, sent = fake_update(text="milk")
    await on_text.callback(update, None)
    assert ASK["brand"] in sent.call_args.args[0]
    brands = [b.text for row in sent.call_args.kwargs["reply_markup"].inline_keyboard for b in row]
    assert set(brands[:2]) == {"Juhayna", "Almarai"} and "Any brand" in brands  # seen in shops before

    async def tap(data):
        update, _ = fake_update(data=data)
        await button.callback(update, None)
        return update.callback_query.edit_message_text.call_args

    assert ASK["type"] in (await tap(f"wz:brand:{brands.index('Juhayna')}")).args[0]
    update, sent = fake_update(text="skimmed")
    await on_text.callback(update, None)
    assert ASK["size"] in sent.call_args.args[0]
    update, sent = fake_update(text="1 litre")
    await on_text.callback(update, None)
    assert ASK["pieces"] in sent.call_args.args[0]
    assert ASK["qty"] in (await tap("wz:pieces:2")).args[0]  # 6
    done = await tap("wz:qty:1")  # 2
    assert "2 × Juhayna skimmed milk 6x1L" in done.args[0] and "brand Juhayna · type skimmed · size 1L · pack of 6" in done.args[0]
    assert "Added: 2 × Juhayna skimmed milk 6x1L" in (await tap("wz:add")).args[0]
    assert [(i.qty, i.query) for i in app.list_items()] == [(2, "Juhayna skimmed milk 6x1L")]

    # Typed in full at step 1, the filled steps are skipped; a pasted list is added as it is.
    update, _ = fake_update(text="/add")
    await add.callback(update, None)
    update, sent = fake_update(text="Al Doha basmati rice 5kg")
    await on_text.callback(update, None)
    assert ASK["pieces"] in sent.call_args.args[0]
    update, sent = fake_update(text=MY_LIST)  # a menu button ends the add
    await on_text.callback(update, None)
    assert "1. 2 × Juhayna skimmed milk 6x1L\n    brand Juhayna · type skimmed · size 1L · pack of 6" in sent.call_args.args[0]
    update, _ = fake_update(text="/add")
    await add.callback(update, None)
    update, _ = fake_update(text="2 rice 5kg\nmilk 1L")
    await on_text.callback(update, None)
    assert [(i.qty, i.query) for i in app.list_items()][-2:] == [(2, "rice 5kg"), (1, "milk 1L")]


def test_set_field_rejects_what_does_not_fit():
    from shopper.bot import set_field
    from shopper.entry import Entry
    e = Entry("milk")
    assert not set_field(e, "size", "big") and not set_field(e, "qty", "two") and not set_field(e, "item", " ")
    assert set_field(e, "size", "6 x 1L") and (e.size, e.pieces) == ("1L", 6)
    assert set_field(e, "size", "30 pcs") and e.pieces == 30


async def test_chat_text_links_names_and_escapes(tmp_path):
    app = core(tmp_path, {"noon": [("Tom & Jerry <Choco> Milk 1L", 30.0)]})
    _, _, rid = await app.run([ItemRequest("Tom Jerry choco milk 1L")])
    text = app.chat_text(rid)
    assert '<a href="https://noon/0">Tom &amp; Jerry &lt;Choco&gt; Milk 1L</a>' in text
    assert "<b>Best plan: EGP 30.00</b>" in text and "http://127.0.0.1" not in text
    app.settings.web_base_url = "https://pc.ts.net"
    assert 'Open the full table</a>' in app.chat_text(rid)


async def test_double_tap_pick_does_not_teach_avoided_words(tmp_path):
    app = core(tmp_path)
    result, _, rid = await app.run([COFFEE])
    jar = next(c.key for c in result.items[0].cards if "jar" in c.forms)
    assert app.feedback(rid, "pick", 0, card=jar) == "ok"
    assert app.feedback(rid, "pick", 0, card=jar) == "stale"
    assert app.memory.avoid_words() == frozenset()


async def test_push_once_across_concurrent_taps(tmp_path):
    import asyncio
    app = core(tmp_path)
    calls = []

    async def push(result):
        calls.append(1)
        await asyncio.sleep(0.05)
        return ["added"]
    app.push_carts = push
    result, _, rid = await app.run([ItemRequest("rice 5kg")])
    out = await asyncio.gather(app.push_once(rid), app.push_once(rid))
    assert len(calls) == 1 and out[0] == ["added"] and out[1][0] == "Already added to your carts:"


def test_unknown_route_and_bad_form_are_pages_and_token_is_encoded(tmp_path):
    app = core(tmp_path)
    app.settings.web_token = "a+b/c"
    client = TestClient(create_app(app))
    page = client.get("/nope")
    assert page.status_code == 404 and "<html" in page.text
    bad = client.post("/r/x/pick?t=a%2Bb%2Fc", data={})
    assert bad.status_code == 400 and "Part of the form was missing" in bad.text
    home = client.get("/", params={"t": "a+b/c"}).text
    assert "/compare?t=a%2Bb/c" in home and app.result_url("r1").endswith("?t=a%2Bb/c")


def test_home_item_rows_compare(tmp_path):
    app = core(tmp_path)
    client = TestClient(create_app(app))
    home = client.get("/?t=k").text
    assert home.count("class=itemrow") == 2 and "Or paste a list" in home and "Add another item" in home
    assert "How shopper reads an item" in home and "<datalist id=brands>" in home and "Size of one" in home
    page = client.post("/compare?t=k", data={"item": ["coffee", "milk", ""], "brand": ["Nescafe", "Juhayna", ""],
                                              "kind": ["classic", "skimmed", ""], "amount": ["200", "1", ""],
                                              "unit": ["g", "L", ""], "pieces": ["", "6", ""], "qty": ["2", "", ""],
                                              "items": ""}, follow_redirects=True)
    rid = page.url.path.split("/")[-1]
    r = app.results[rid].items
    assert [(x.request.query, x.request.qty) for x in r] == [("Nescafe classic coffee 200g", 2), ("Juhayna skimmed milk 6x1L", 1)]
    assert "<i>Brand</i> Nescafe" in page.text and "<i>Pack</i> 6 pieces" in page.text
    short = client.post("/compare?t=k", data={"item": ["rice"], "items": ""}, follow_redirects=True)  # other fields absent
    assert "<i>Brand</i> any" in short.text
    empty = client.post("/compare?t=k", data={"item": [""], "items": ""})
    assert empty.status_code == 400 and "Fill in at least one item" in empty.text
