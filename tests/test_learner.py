from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from shopper.learner import MIN_ANSWERS, Learner, features
from shopper.memory import offer_key
from shopper.models import ItemRequest, Offer
from shopper.store import Store
from shopper.web import create_app
from test_memory import COFFEE, core


def test_answers_move_the_probability(tmp_path):
    lr = Learner(Store(tmp_path / "db.sqlite3").db)
    req = ItemRequest("Nescafe classic 200g")
    sachet = Offer("noon", "Nescafe Classic Sachet 200grams", 341, "u")
    before = lr.probability(features(req, sachet))
    for _ in range(8):
        lr.learn(req, sachet, False)
    assert lr.probability(features(req, sachet)) < before - 0.2
    again = Learner(lr.db)  # stored
    assert again.answers == 8 and abs(again.probability(features(req, sachet)) - lr.probability(features(req, sachet))) < 1e-9


def test_filter_only_after_enough_answers(tmp_path):
    lr = Learner(Store(tmp_path / "db.sqlite3").db)
    req = ItemRequest("milk 1L")
    bad = Offer("a", "Milk Chocolate Bar 1L", 10, "u")
    assert lr.keep(req, [bad]) is None
    for _ in range(MIN_ANSWERS):
        lr.learn(req, bad, False)
    assert lr.active and lr.keep(req, [bad])(bad) is False


async def test_yes_no_on_the_page_pins_or_rejects(tmp_path):
    app = core(tmp_path, {"seoudi": [("Nescafe Classic Coffee Jar 200g", 310.0)], "spinneys": [("Nescafe Classic Jar 200g", 289.0)]})
    client = TestClient(create_app(app))
    page = client.post("/compare?t=k", data={"item": ["Nescafe classic 200g"]}, follow_redirects=True)
    assert "Right product?" in page.text and "match " in page.text
    rid = page.url.path.split("/")[-1]
    m = app.results[rid].best.assignments[0]
    no = client.post(f"/r/{rid}/deny?t=k", data={"item": 0, "platform": m.offer.platform, "offer": offer_key(m.offer)},
                     follow_redirects=True)
    assert app.learner.answers == 1 and app.results[rid].best.assignments[0].offer.platform != m.offer.platform
    m2 = app.results[rid].best.assignments[0]
    client.post(f"/r/{rid}/confirm?t=k", data={"item": 0, "platform": m2.offer.platform, "offer": offer_key(m2.offer)})
    assert app.results[rid].items[0].pinned and app.learner.answers >= 2
    assert "your product" in client.get(f"/r/{rid}?t=k").text


async def test_telegram_check_buttons(tmp_path):
    from telegram.ext import CallbackQueryHandler

    from shopper.bot import build_bot, check_keyboard
    app = core(tmp_path, {"seoudi": [("Nescafe Classic Coffee Jar 200g", 310.0)], "spinneys": [("Nescafe Classic Jar 200g", 289.0)]})
    app.settings.telegram_token, app.settings.allowed_chat_ids = "123456:TEST", {42}
    result, _, rid = await app.run([COFFEE])
    kb = check_keyboard(result, rid)
    yes, no = kb.inline_keyboard[0]
    assert yes.text.startswith("Yes: Nescafe Classic Jar 200g") and len(no.callback_data.encode()) <= 64
    handler = next(h for h in build_bot(app).handlers[0] if isinstance(h, CallbackQueryHandler))
    sent = AsyncMock()
    q = SimpleNamespace(data=no.callback_data, answer=AsyncMock(), edit_message_reply_markup=AsyncMock())
    upd = SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=42), effective_message=SimpleNamespace(reply_text=sent))
    await handler.callback(upd, None)
    assert "Next best for Nescafe classic 200g" in sent.call_args.args[0] and "Seoudi" in sent.call_args.args[0]
