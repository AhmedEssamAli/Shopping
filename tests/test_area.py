"""Seoudi delivery area: districts from Seoudi's own list, chosen on the home page."""
import json

import httpx
from fastapi.testclient import TestClient

from shopper.adapters.magento import seoudi
from shopper.app import App
from shopper.config import PlatformConfig, PlatformFees, Settings
from shopper.report import branch_note
from shopper.web import create_app

# Shape of Seoudi's answer to city(id){available_districts{...}} (2026-10-20), trimmed.
CITY = {"data": {"city": {"name": "Fifth Settlement", "available_districts": [
    {"id": "3105", "name": "El Lotus El Shamaleya", "available_store": {"name": "HydePark", "storeSource": {"source_code": "46"}}},
    {"id": "3108", "name": "South El-Lotus", "available_store": None},
    {"id": "3111", "name": "Investors Area 2", "available_store": {"name": "District 5", "storeSource": {"source_code": "40"}}},
]}}}


def shop():
    a = seoudi((30.0074, 31.4913))
    a.area_cities = (31,)
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append((request.headers.get("sourcecode"), body["query"]))
        if "available_districts" in body["query"]:
            return httpx.Response(200, json=CITY)
        return httpx.Response(200, json={"data": {"products": {"items": []}}})

    a._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return a, seen


async def test_chosen_district_sets_the_branch_header():
    a, seen = shop()
    areas = await a.areas()
    assert [(x["district"], x["store"], x["source"]) for x in areas] == [
        ("El Lotus El Shamaleya", "HydePark", "46"), ("Investors Area 2", "District 5", "40"), ("South El-Lotus", None, "")]
    a.use_area(areas[0])
    await a.search("milk")
    assert seen[-1][0] == "46"  # no findStore lookup: the chosen branch is used
    assert "HydePark branch, which serves El Lotus El Shamaleya" in branch_note(a)
    a.use_area(areas[2])
    await a.search("milk")
    assert seen[-1][0] is None and "without a branch" in branch_note(a)


async def test_area_is_saved_and_restored(tmp_path):
    s = Settings(home=tmp_path, platforms={"seoudi": PlatformConfig("seoudi", "Seoudi Market", "live", True, PlatformFees())})
    s.web_token = "k"
    app = App(s)
    a, _ = shop()
    app.adapters = {"seoudi": a}
    assert (await app.set_area("seoudi", "3105"))["store"] == "HydePark"
    assert await app.set_area("seoudi", "nope") is None
    again = App(s)  # restart: the saved area is applied to the new adapter
    assert again.adapters["seoudi"].area == "El Lotus El Shamaleya" and again.adapters["seoudi"].branch == "HydePark"


def test_home_page_area_picker(tmp_path):
    s = Settings(home=tmp_path, platforms={"seoudi": PlatformConfig("seoudi", "Seoudi Market", "live", True, PlatformFees())})
    s.web_token = "k"
    app = App(s)
    a, _ = shop()
    app.adapters = {"seoudi": a}
    client = TestClient(create_app(app))
    page = client.get("/?t=k").text
    assert "Your delivery area" in page and "South El-Lotus — no branch" in page and "<optgroup label='Fifth Settlement'>" in page
    r = client.post("/area/seoudi?t=k", data={"district": "3111"}, follow_redirects=False)
    assert r.status_code == 303 and a.branch == "District 5"
    assert "selected>Investors Area 2" in client.get("/?t=k").text
    assert client.post("/area/seoudi?t=k", data={"district": ""}).status_code == 400
