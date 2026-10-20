"""Grocers on Magento 2 expose a public GraphQL catalogue: Gourmet, Seoudi, Spinneys, HyperOne.

Verified 2026-10-18 from outside Egypt: search returns name, SKU, URL key, stock, photo and
EGP price with no login.

Branches (verified 2026-10-20): Seoudi and Spinneys answer ``findStore(lat, lng)`` with the
branch that serves a location, and Seoudi's own site sends that branch's ``source_code`` in a
``sourcecode`` header on every catalogue call; with it, the assortment and stock change
(Seoudi: "District 5" for the default New Cairo point). shopper does the same, using
AREA_LAT / AREA_LON. That Spinneys honours the header is not verified.

Seoudi maps areas, not points (verified 2026-10-20): each city lists its districts, and each
district names the branch that serves it (``available_store``), or none. The default New Cairo
point falls in "Investors Area 2" -> District 5. You choose your district on the home page;
shopper then uses that district's branch, exactly as Seoudi's own area picker does.
"""
from __future__ import annotations

import logging

import httpx

from ..models import Offer
from ..units import parse_size
from .base import Adapter

log = logging.getLogger(__name__)

SEARCH_QUERY = """
query ($q: String!, $n: Int!) {
  products(search: $q, pageSize: $n) {
    items {
      name sku url_key stock_status small_image { url } %s
      price_range { minimum_price { final_price { value currency } regular_price { value } } }
    }
  }
}
"""


class MagentoAdapter(Adapter):
    def __init__(self, name: str, label: str, graphql_url: str, product_base: str, login_url: str = "",
                 size_field: str = "", branch_at: tuple[float, float] | None = None):
        self.name = name
        self.size_field = size_field  # catalogue attribute holding the pack size, when names omit it
        self.label = label
        self.graphql_url = graphql_url
        self.product_base = product_base.rstrip("/")
        self.login_url = login_url
        self.branch_at = branch_at  # (lat, lon) to look up the serving branch, for shops that have them
        self.branch: str | None = None  # the branch name, once found (shown on the page)
        self._source: str | None = None
        self.area: str | None = None  # the district you chose (Seoudi), shown with the branch
        self.area_cities: tuple[int, ...] = ()  # cities to offer in the area picker
        self._areas: list[dict] | None = None
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=25,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (shopper personal price check)"},
            )
        return self._client

    async def _source_code(self) -> str | None:
        """The branch that delivers to AREA_LAT/AREA_LON, looked up once."""
        if self.branch_at is None or self._source is not None:
            return self._source
        lat, lon = self.branch_at
        q = '{findStore(lat:"%s",lng:"%s"){store{name storeSource{source_code}}}}' % (lat, lon)
        try:
            r = await self._http().post(self.graphql_url, json={"query": q})
            store = r.json()["data"]["findStore"]["store"]
            self._source, self.branch = store["storeSource"]["source_code"], store["name"]
        except Exception as e:  # no branch: search the default store rather than fail
            log.warning("%s: no branch found for %s,%s (%s); using the default store", self.label, lat, lon, e)
            self._source = ""
        return self._source

    async def areas(self) -> list[dict]:
        """Districts of ``area_cities`` with the branch that serves each, from the shop's own list."""
        if self._areas is None:
            out = []
            for cid in self.area_cities:
                q = '{city(id:"%s"){name available_districts{id name available_store{name storeSource{source_code}}}}}' % cid
                r = await self._http().post(self.graphql_url, json={"query": q})
                city = r.json()["data"]["city"]
                for d in city["available_districts"] or []:
                    store = d.get("available_store") or {}
                    out.append({"id": str(d["id"]), "city": city["name"].strip(), "district": d["name"].strip(),
                                "store": store.get("name"), "source": (store.get("storeSource") or {}).get("source_code") or ""})
            self._areas = sorted(out, key=lambda a: (a["city"], a["district"].lower()))
        return self._areas

    def use_area(self, area: dict) -> None:
        """Search as the branch of the district you chose. A district without a branch uses the default store."""
        self.area, self.branch, self._source = area["district"], area.get("store"), area.get("source") or ""

    async def search(self, query: str, limit: int = 12) -> list[Offer]:
        source = await self._source_code()
        headers = {"sourcecode": source} if source else {}
        r = await self._http().post(self.graphql_url, headers=headers,
                                    json={"query": SEARCH_QUERY % self.size_field, "variables": {"q": query, "n": limit}})
        r.raise_for_status()
        return self.parse(r.json())

    def parse(self, payload: dict) -> list[Offer]:
        if payload.get("errors") and not payload.get("data"):
            raise RuntimeError(f"{self.name}: {payload['errors'][0].get('message')}")
        items = ((payload.get("data") or {}).get("products") or {}).get("items") or []
        offers = []
        for it in items:
            mp = (it.get("price_range") or {}).get("minimum_price") or {}
            price = (mp.get("final_price") or {}).get("value")
            if price is None:
                continue
            name = " ".join(it["name"].split())
            size_text = (it.get(self.size_field) or "").strip() if self.size_field else ""
            if size_text and not parse_size(name):
                name = f"{name} - {size_text}"
            offers.append(
                Offer(
                    platform=self.name,
                    name=name,
                    price=float(price),
                    regular_price=(mp.get("regular_price") or {}).get("value"),
                    url=f"{self.product_base}/{it['url_key']}",
                    sku=it.get("sku", ""),
                    in_stock=it.get("stock_status") == "IN_STOCK",
                    image=((it.get("small_image") or {}).get("url") or ""),
                )
            )
        return offers

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


def gourmet() -> MagentoAdapter:
    return MagentoAdapter("gourmet", "Gourmet Egypt", "https://gourmetegypt.com/graphql", "https://gourmetegypt.com",
                          login_url="https://gourmetegypt.com", size_field="product_weight")


# Seoudi city ids in and around New Cairo (from its store list, 2026-10-20): First Settlement,
# Fifth Settlement, District 5, Al Rehab, Madinty, El Shorouk, Sodic East.
SEOUDI_CITIES = (25, 31, 125, 87, 121, 118, 135)


def seoudi(at: tuple[float, float] | None = None) -> MagentoAdapter:
    a = MagentoAdapter("seoudi", "Seoudi Market", "https://mcprod.seoudisupermarket.com/graphql",
                       "https://seoudisupermarket.com/en", login_url="https://seoudisupermarket.com/en", branch_at=at)
    a.area_cities = SEOUDI_CITIES
    return a


def hyperone() -> MagentoAdapter:
    # Public Magento GraphQL verified 2026-10-20; product pages are /en/product/<url_key>.
    return MagentoAdapter("hyperone", "HyperOne", "https://mcprod.hyperone.com.eg/graphql",
                          "https://www.hyperone.com.eg/en/product", login_url="https://www.hyperone.com.eg/en")


def spinneys(at: tuple[float, float] | None = None) -> MagentoAdapter:
    return MagentoAdapter("spinneys", "Spinneys Egypt", "https://mcprod.spinneys-egypt.com/graphql",
                          "https://spinneys-egypt.com/en", login_url="https://spinneys-egypt.com/en", branch_at=at)
