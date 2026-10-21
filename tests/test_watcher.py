from shopper.app import App
from shopper.models import ItemRequest, Match, Offer
from shopper.store import Watch
from shopper.watcher import alert_for, check_watches, parse_watch
from test_service import ADAPTERS, Fake, settings


def test_parse_watch():
    assert parse_watch("Nescafe classic 200g @ 250") == ("Nescafe classic 200g", 250.0)
    assert parse_watch("Nescafe classic 200g@249,5") == ("Nescafe classic 200g", 249.5)
    assert parse_watch("  rice 5kg ") == ("rice 5kg", None)


def w(target=None, last=None):
    return Watch(1, 42, "milk 1L", target, last, None, None, None)


def m(price):
    return Match(ItemRequest("milk 1L"), Offer("a", "Milk 1L", price, "https://a/1"), 100, True)


def test_alert_on_crossing_target_only():
    assert "TARGET REACHED" in alert_for(w(target=50, last=60), m(49), "A")
    assert alert_for(w(target=50, last=48), m(47.9), "A") is None  # already below target, drop under 1%
    assert alert_for(w(target=50, last=48), m(48), "A") is None  # already below target, unchanged


def test_alert_on_drop_without_target():
    assert "PRICE DROP" in alert_for(w(last=60), m(55), "A")
    assert alert_for(w(last=60), m(59.7), "A") is None  # under 1%
    assert alert_for(w(), m(55), "A") is None  # first check sets the baseline


async def test_check_watches_updates_and_alerts(tmp_path):
    core = App(settings(tmp_path))
    core.adapters = dict(ADAPTERS)
    wid = core.store.add_watch(42, "Juhayna full cream milk 1L", 45)
    assert await check_watches(core) == []  # cheapest exact is 50 at "b"
    assert core.store.list_watches()[0].last_price == 50
    core.adapters["b"] = Fake("b", [("Juhayna Full Cream Milk - 1 Liter", 44)])
    alerts = await check_watches(core)
    assert alerts and alerts[0][0] == 42 and "TARGET REACHED" in alerts[0][1]
    assert core.store.delete_watch(42, wid) and core.store.list_watches() == []


def test_target_watch_alerts_only_on_reaching_target():
    assert alert_for(w(target=40, last=60), m(55), "A") is None  # dropped, but target not reached
