"""Grocers on Magento 2 expose a public GraphQL catalogue: Gourmet, Seoudi, Spinneys.

Verified 2026-10-18 from outside Egypt: search returns name, SKU, URL key, stock and
EGP price with no login. Prices shown are the site's default store view; branch-level
pricing (if any) is not modelled yet.
"""
from __future__ import annotations

import httpx

from ..models import Offer
from ..units import parse_size
from .base import Adapter

SEARCH_QUERY = """
query ($q: String!, $n: Int!) {
  products(search: $q, pageSize: $n) {
    items {
      name sku url_key stock_status %s
      price_range { minimum_price { final_price { value currency } regular_price { value } } }
    }
  }
}
"""


class MagentoAdapter(Adapter):
    def __init__(self, name: str, label: str, graphql_url: str, product_base: str, login_url: str = "",
                 size_field: str = ""):
        self.name = name
        self.size_field = size_field  # catalogue attribute holding the pack size, when names omit it
        self.label = label
        self.graphql_url = graphql_url
        self.product_base = product_base.rstrip("/")
        self.login_url = login_url
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=25,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (shopper personal price check)"},
            )
        return self._client

    async def search(self, query: str, limit: int = 12) -> list[Offer]:
        r = await self._http().post(self.graphql_url, json={"query": SEARCH_QUERY % self.size_field, "variables": {"q": query, "n": limit}})
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


def seoudi() -> MagentoAdapter:
    return MagentoAdapter("seoudi", "Seoudi Market", "https://mcprod.seoudisupermarket.com/graphql",
                          "https://seoudisupermarket.com/en", login_url="https://seoudisupermarket.com/en")


def spinneys() -> MagentoAdapter:
    return MagentoAdapter("spinneys", "Spinneys Egypt", "https://mcprod.spinneys-egypt.com/graphql",
                          "https://spinneys-egypt.com/en", login_url="https://spinneys-egypt.com/en")
