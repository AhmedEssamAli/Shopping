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
    assert rows[0].callback_data == f"push:{rid}" and rows[-1].url.startswith("https://pc.tail.ts.net/r/")


def test_list_keyboard_rows():
    kb = list_keyboard(6).inline_keyboard
    assert [b.text for b in kb[0]] == ["Remove 1", "Remove 2", "Remove 3", "Remove 4"]
    assert [b.callback_data for b in kb[-1]] == ["cmp", "clr"] and list_keyboard(0).inline_keyboard == ()


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

    update, _ = fake_update(data="rm:0")
    await on_button.callback(update, None)
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
    client.post(f"/r/{rid}/pick?t=k", data={"item": 0, "card": 0})
    home = client.get("/?t=k").text
    assert "Products you chose before" in home and "data-line='Nescafe classic 200g'" in home
    assert "Recent comparisons" in home and f"/r/{rid}?t=k" in home and "2 items: Nescafe classic 200g, rice 5kg" in home
    assert "<b>Live</b>" in home
    client.post("/list/remove?t=k", data={"index": 0})
    assert [i.query for i in app.list_items()] == ["rice 5kg"]
    assert client.post("/list/clear").status_code == 404  # token required
    assert client.post("/list/wipe?t=k").status_code == 404
