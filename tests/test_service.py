import json
from pathlib import Path

from fastapi.testclient import TestClient

from shopper.adapters.base import Adapter, NotCalibrated
from shopper.app import App
from shopper.config import PlatformConfig, Settings
from shopper.models import ItemRequest, Offer, PlatformFees
from shopper.report import to_html, to_text
from shopper.service import compare
from shopper.web import create_app


class Fake(Adapter):
    def __init__(self, name, catalogue):
        self.name, self.label, self.catalogue = name, name.upper(), catalogue

    async def search(self, query, limit=12):
        return [Offer(self.name, n, p, f"https://{self.name}/{i}") for i, (n, p) in enumerate(self.catalogue)]


def rows(requests) -> dict:
    """The item form's fields for these lines, as the review step posts them."""
    return {"item": [r.query for r in requests], "qty": [str(r.qty) for r in requests]}


class Broken(Adapter):
    name = label = "broken"

    async def search(self, query, limit=12):
        raise NotCalibrated("not calibrated")


def settings(tmp_path, fees=None):
    fees = fees or {}
    plats = {n: PlatformConfig(n, n.upper(), "live", True, fees.get(n, PlatformFees())) for n in ("a", "b", "broken")}
    return Settings(home=tmp_path, platforms=plats)


ADAPTERS = {
    "a": Fake("a", [("Juhayna Full Cream Milk 1L", 60), ("Egyptian Rice 5kg", 200)]),
    "b": Fake("b", [("Juhayna Full Cream Milk - 1 Liter", 50), ("Egyptian Rice 5 kg", 230)]),
    "broken": Broken(),
}
REQS = [ItemRequest("Juhayna full cream milk 1L", 2), ItemRequest("Egyptian rice 5kg", 1)]


async def test_compare_splits_and_reports_errors(tmp_path):
    s = settings(tmp_path)
    r = await compare(REQS, s, ADAPTERS)
    assert r.best.platforms == ("a", "b")
    assert r.best.total == 2 * 50 + 200
    assert "broken" in r.errors
    assert set(r.unverified_fees) == {"a", "b", "broken"}


async def test_compare_fees_flip_to_single(tmp_path):
    fees = {n: PlatformFees(delivery_fee=40, verified=True) for n in ("a", "b")}
    r = await compare(REQS, settings(tmp_path, fees), ADAPTERS)
    assert r.best.platforms == ("a",)  # 120+200+40 = 360 beats 100+200+80 = 380
    assert r.single.platforms == ("a",)


async def test_report_renders(tmp_path):
    s = settings(tmp_path)
    r = await compare(REQS, s, ADAPTERS)
    text = to_text(r, s, ADAPTERS, "http://x/r/1")
    assert "BEST PLAN: A + B" in text and "Full table: http://x/r/1" in text
    html = to_html(r, s, ADAPTERS)
    assert "class=labels" in html and "https://b/0" in html and "If you buy everything in one shop" in html


def test_web_token_and_flow(tmp_path):
    s = settings(tmp_path)
    s.web_token = "secret"
    core = App(s)
    core.adapters = ADAPTERS
    client = TestClient(create_app(core))
    assert client.get("/").status_code == 404
    assert client.get("/?t=secret").status_code == 200
    resp = client.post("/compare?t=secret", data={"item": ["Juhayna full cream milk 1L", "Egyptian rice 5kg"], "qty": ["2", ""], "max_orders": 2},
                       follow_redirects=True)
    assert resp.status_code == 200 and "Basket comparison" in resp.text
    assert client.get("/r/nope?t=secret").status_code == 404


def test_platforms_toml_loads():
    from shopper.config import load_platforms, REPO_ROOT
    plats = load_platforms(REPO_ROOT / "config" / "platforms.toml")
    assert plats["seoudi"].status == "live" and plats["breadfast"].status == "app"


def test_bot_builds_offline(tmp_path):
    from shopper.bot import build_bot
    s = settings(tmp_path)
    s.telegram_token = "123456:TEST"
    s.allowed_chat_ids = {42}
    bot = build_bot(App(s))
    commands = {c for h in bot.handlers[0] for c in getattr(h, "commands", ())}
    assert {"start", "add", "list", "remove", "clear", "compare"} <= commands


def test_bot_has_watch_commands(tmp_path):
    from shopper.bot import build_bot
    s = settings(tmp_path)
    s.telegram_token = "123456:TEST"
    commands = {c for h in build_bot(App(s)).handlers[0] for c in getattr(h, "commands", ())}
    assert {"watch", "watches", "unwatch"} <= commands


def test_talabat_shops_expand_to_platforms():
    from shopper.config import load_platforms, REPO_ROOT
    plats = load_platforms(REPO_ROOT / "config" / "platforms.toml")
    mart = plats["talabat_793312"]
    assert "talabat" not in plats and mart.label == "talabat Mart" and mart.status == "live"
    assert mart.options["area_id"] == 8192 and mart.options["slug"].startswith("talabat-mart")
    assert mart.fees.min_order == 40 and "talabat_743103" in plats


class Pushy(Fake):
    supports_cart_push = True

    async def add_to_cart(self, lines):
        return [f"added: {q} × {o.name}" for o, q in lines]


def test_web_is_an_installable_app_with_cart_button(tmp_path):
    s = settings(tmp_path)
    s.web_token = "secret"
    core = App(s)
    core.adapters = {"a": Pushy("a", [("Juhayna Full Cream Milk 1L", 60)])}
    client = TestClient(create_app(core))
    home = client.get("/?t=secret").text
    assert "/manifest.webmanifest?t=secret" in home and "serviceWorker" in home
    assert client.get("/manifest.webmanifest").status_code == 404  # carries the token, so it is guarded
    m = client.get("/manifest.webmanifest?t=secret").json()
    assert m["start_url"] == "/?t=secret" and m["display"] == "standalone" and len(m["icons"]) == 2
    png = client.get("/icon-512.png")
    assert png.headers["content-type"] == "image/png" and png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert "offline" in client.get("/sw.js").text and client.get("/offline").status_code == 200

    page = client.post("/compare?t=secret", data={"item": ["Juhayna full cream milk 1L"], "max_orders": 2},
                       follow_redirects=True)
    assert "Add 1 item to cart" in page.text and "/push-shop?t=secret" in page.text
    rid = page.url.path.split("/")[-1]
    pushed = client.post(f"/r/{rid}/push-shop?t=secret", data={"shop": "a"}, follow_redirects=True)
    assert "added: 1 × Juhayna Full Cream Milk 1L" in pushed.text and "Done: order placed" in pushed.text  # a full cart is not an order
    assert "already added from its order card" in client.post(f"/r/{rid}/push?t=secret").text
    assert client.post(f"/r/{rid}/push").status_code == 404
