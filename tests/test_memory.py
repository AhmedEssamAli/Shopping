"""Option C: pick the product once; shopper remembers it and learns the words you avoid."""
from fastapi.testclient import TestClient

from shopper.app import App
from shopper.config import PlatformConfig, Settings
from shopper.memory import Memory, product_cards, request_key
from shopper.models import ItemRequest, Offer, PlatformFees
from shopper.web import create_app
from test_service import Fake

CATALOGUE = {
    "noon": [("Nescafe Classic Sachet 200grams", 341.0), ("Nescafe Classic Jar 190grams", 449.95)],
    "amazon": [("Nescafe Classic Instant Coffee Pouch 200g", 261.99)],
    "gourmet": [("Nestlé Nescafé Classic Instant Coffee Jar - 200gm", 300.0)],
    "seoudi": [("Nescafe Classic Coffee Jar 200g", 310.0)],
}
COFFEE = ItemRequest("Nescafe classic 200g")


def core(tmp_path, catalogue=CATALOGUE):
    plats = {n: PlatformConfig(n, n.title(), "live", True, PlatformFees()) for n in catalogue}
    s = Settings(home=tmp_path, platforms=plats)
    s.web_token = "k"
    app = App(s)
    app.adapters = {n: Fake(n, items) for n, items in catalogue.items()}
    return app


def offers():
    return [Offer(p, n, price, f"https://{p}/{i}") for p, items in CATALOGUE.items() for i, (n, price) in enumerate(items)]


def test_pack_types_are_separate_products():
    cards = product_cards(COFFEE, offers())
    exact = {c.label for c in cards if c.exact}
    assert exact == {"200g, sachet", "200g, pouch", "200g, jar"}
    jar = next(c for c in cards if c.label == "200g, jar")
    assert set(jar.per_platform()) == {"gourmet", "seoudi"}


def test_key_ignores_case_accents_and_size_spelling():
    assert request_key(ItemRequest("NESCAFÉ Classic 200 gm")) == request_key(COFFEE)


async def test_pick_once_then_the_plan_uses_your_product(tmp_path):
    app = core(tmp_path)
    result, text, rid = await app.run([COFFEE])
    assert result.items[0].needs_pick and "Not final" in text
    assert result.best.assignments[0].offer.price == 261.99  # cheapest exact today: the pouch

    jar = next(j for j, c in enumerate(result.items[0].cards) if c.label == "200g, jar")
    assert app.feedback(rid, "pick", 0, card=result.items[0].cards[jar].key) == "ok"
    m = result.best.assignments[0]
    assert m.offer.name.startswith("Nestlé") and m.pinned and not result.items[0].needs_pick
    pouch = result.items[0].matches["amazon"]
    assert pouch is None or not pouch.exact  # a pouch is not your jar

    again, text2, _ = await app.run([ItemRequest("nescafe CLASSIC 200 gm", 2)])
    assert again.items[0].pinned and "Not final" not in text2
    assert again.best.assignments[0].offer.price == 300.0


async def test_passed_over_pack_words_become_avoided(tmp_path):
    app = core(tmp_path)
    mem: Memory = app.memory
    result, _, rid = await app.run([COFFEE])
    app.feedback(rid, "pick", 0, card=next(c.key for c in result.items[0].cards if c.label == "200g, jar"))
    assert "sachet" not in mem.avoid_words()  # once is not a habit
    tea = {"noon": [("Lipton Yellow Label Tea Sachet 100 bags", 120.0), ("Lipton Yellow Label Tea Box 100 bags", 130.0)]}
    app2 = core(tmp_path / "x", tea)
    app2.memory = mem  # same household memory
    r2, _, rid2 = await app2.run([ItemRequest("Lipton yellow label tea 100 bags")])
    app2.feedback(rid2, "pick", 0, card=next(c.key for c in r2.items[0].cards if "box" in c.forms))
    assert "sachet" in mem.avoid_words()
    r3, _, _ = await app2.run([ItemRequest("Lipton tea 100 bags")])  # a new line, never picked
    assert not r3.items[0].needs_pick and "Box" in r3.items[0].matches["noon"].offer.name


async def test_not_this_removes_a_listing_for_that_line(tmp_path):
    app = core(tmp_path)
    result, _, rid = await app.run([COFFEE])
    assert result.items[0].matches["noon"].offer.name == "Nescafe Classic Sachet 200grams"
    from shopper.memory import offer_key
    stale = app.feedback(rid, "reject", 0, platform="noon", offer="00000000")
    assert stale == "stale" and result.items[0].matches["noon"].offer.name == "Nescafe Classic Sachet 200grams"
    assert app.feedback(rid, "reject", 0, platform="noon", offer=offer_key(result.items[0].matches["noon"].offer)) == "ok"
    assert all("Sachet" not in o.name for c in result.items[0].cards for o in c.offers)  # gone from the cards too
    assert result.items[0].matches["noon"] is None or "Sachet" not in result.items[0].matches["noon"].offer.name
    again, _, _ = await app.run([COFFEE])
    assert again.items[0].matches["noon"] is None or "Sachet" not in again.items[0].matches["noon"].offer.name


def test_web_buttons_pick_and_change(tmp_path):
    app = core(tmp_path)
    client = TestClient(create_app(app))
    page = client.post("/compare?t=k", data={"items": "Nescafe classic 200g", "max_orders": 2}, follow_redirects=True)
    assert "Choose your products" in page.text and "This one" in page.text and "not this" in page.text
    rid = page.url.path.split("/")[-1]
    key = app.results[rid].items[0].cards[0].key
    picked = client.post(f"/r/{rid}/pick?t=k", data={"item": 0, "card": key}, follow_redirects=True)
    assert "your product" in picked.text and "1 chosen product," in picked.text
    changed = client.post(f"/r/{rid}/forget?t=k", data={"item": 0}, follow_redirects=True)
    assert "Choose your products" in changed.text
    assert client.post(f"/r/{rid}/pick", data={"item": 0, "card": key}).status_code == 404  # token required
    assert client.post(f"/r/{rid}/delete?t=k", data={"item": 0}).status_code == 404


async def test_telegram_buttons_pick_and_choose_again(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from telegram.ext import CallbackQueryHandler

    from shopper.bot import build_bot, parse_callback, pick_keyboard

    app = core(tmp_path)
    app.settings.telegram_token, app.settings.allowed_chat_ids = "123456:TEST", {42}
    result, _, rid = await app.run([COFFEE])
    kb = pick_keyboard(app, result, rid, 0)
    labels = [row[0].text for row in kb.inline_keyboard]
    assert labels[0].startswith("200g, pouch — 261.99 Amazon") and len(kb.inline_keyboard) == 3
    jar = next(row[0] for row in kb.inline_keyboard if "jar" in row[0].text)
    assert parse_callback(jar.callback_data)[:3] == ("pick", rid, 0) and parse_callback("pk:x:y:z") is None

    handler = next(h for h in build_bot(app).handlers[0] if isinstance(h, CallbackQueryHandler))
    sent = AsyncMock()
    query = SimpleNamespace(data=jar.callback_data, answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=42),
                             effective_message=SimpleNamespace(reply_text=sent))
    await handler.callback(update, None)
    assert "Chosen for Nescafe classic 200g: 200g, jar" in query.edit_message_text.call_args.args[0]
    assert "Updated plan" in sent.call_args.args[0] and "300.00" in sent.call_args.args[0]
    assert result.items[0].pinned

    query.data = f"fg:{rid}:0"
    await handler.callback(update, None)
    assert not result.items[0].pinned and "Which one do you mean?" in query.edit_message_text.call_args.args[0]

    query.data = "pk:gone:0:0"
    await handler.callback(update, None)
    assert "no longer open" in query.answer.call_args.args[0]


async def test_stale_pick_button_cannot_choose_another_product(tmp_path):
    app = core(tmp_path)
    result, _, rid = await app.run([COFFEE])
    sachet = next(c.key for c in result.items[0].cards if "sachet" in c.forms)
    jar = next(c.key for c in result.items[0].cards if "jar" in c.forms)
    assert app.feedback(rid, "pick", 0, card=jar) == "ok"
    assert app.memory.recall(COFFEE).canonical.endswith("jar")
    app.feedback(rid, "forget", 0)  # cards come back, sorted differently than the old message
    assert app.feedback(rid, "pick", 0, card=sachet) == "ok"  # an old button still means sachet
    assert app.memory.recall(COFFEE).canonical.endswith("sachet")
    assert app.feedback(rid, "pick", 0, card="deadbeef") == "stale"
    assert sachet != jar


def test_cards_split_rice_kinds():
    offers = [Offer("a", "Shahy Basmati Rice 5kg", 700, "u1"), Offer("b", "Al Doha Egyptian Rice 5kg", 220, "u2")]
    cards = product_cards(ItemRequest("rice 5kg"), offers)
    assert {c.label for c in cards} == {"5kg, pack type not stated, basmati", "5kg, pack type not stated, egyptian"}
