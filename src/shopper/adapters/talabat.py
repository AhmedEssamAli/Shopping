"""Any talabat grocery shop (talabat Mart, Carrefour, Spinneys, Metro, Lulu, ...) for one
branch, read from talabat's own Next.js data routes.

Verified 2026-10-18 from a capture on the home PC and live from the cloud: the area's
grocery listing names every shop that delivers to you; each branch menu lists
categories, and each sub-category pages its items (20 per page) with title, price,
originalPrice, sku and stockAmount. talabat's web has no item search for these shops, so
the adapter snapshots each branch catalogue at most once per ``max_age_hours`` and
searches it locally. talabat Mart is ~9,000 items (~550 requests, ~3.5 min).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from ..matching import has_word, tokens
from ..models import Offer, PlatformFees, now_utc
from .base import Adapter

log = logging.getLogger(__name__)
DATA = "https://www.talabat.com/_next/data/manifests"
SITE = "https://www.talabat.com/egypt/grocery"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
# talabat answers 429 when several catalogues download at once (seen on the home PC,
# 2026-10-19). So: one shop at a time, 2 requests in flight, a gap after each, and
# back off on 429 / 5xx.
REQUEST_GAP_S = 0.3
RETRIES = 6
_locks: dict[int, asyncio.Lock] = {}


def _download_lock() -> asyncio.Lock:
    """One catalogue download at a time across all talabat shops (per event loop)."""
    loop = asyncio.get_running_loop()
    return _locks.setdefault(id(loop), asyncio.Lock())


class TalabatShopAdapter(Adapter):
    needs_browser = False

    def __init__(self, name: str, label: str, area_id: int, branch_id: int, branch_slug: str, cache_dir: Path,
                 max_age_hours: float = 24, concurrency: int = 2, skip_categories: tuple[str, ...] = ()):
        self.name, self.label = name, label
        self.area_id, self.branch_id, self.branch_slug = area_id, branch_id, branch_slug
        self.cache_path = cache_dir / f"talabat_{branch_id}.json"
        self.max_age = timedelta(hours=max_age_hours)
        self.concurrency = concurrency
        self.skip = set(skip_categories)
        self.login_url = f"{SITE}/{branch_id}/{branch_slug}?aid={area_id}"
        self._snapshot: dict | None = None
        self._bg: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None

    # ---- talabat data routes -------------------------------------------------
    def _params(self, **extra) -> dict:
        return {"aid": self.area_id, "countrySlug": "egypt", "vertical": "grocery",
                "branchId": self.branch_id, "branchSlug": self.branch_slug, **extra}

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30, headers={"User-Agent": UA}, follow_redirects=False)
        return self._client

    async def _get(self, route: str, **extra) -> dict:
        for attempt in range(RETRIES):
            try:
                r = await self._http().get(f"{DATA}/{route}", params=self._params(**extra))
            except httpx.TransportError:
                if attempt == RETRIES - 1:
                    raise
                await asyncio.sleep(2 * 2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == RETRIES - 1:
                    r.raise_for_status()
                wait = r.headers.get("Retry-After", "")
                await asyncio.sleep(float(wait) if wait.isdigit() else min(60.0, 3 * 2 ** attempt))
                continue
            r.raise_for_status()
            return r.json()["pageProps"]
        raise RuntimeError("unreachable")

    @staticmethod
    def parse_menu(props: dict) -> tuple[dict, list[tuple[str, str, int]]]:
        st = props["initialState"]
        subs = [(c["slug"], s["slug"], s.get("count", 0)) for c in st["categories"] for s in c.get("subCategories") or []]
        return st.get("groceryStore") or {}, subs

    @staticmethod
    def parse_items(props: dict) -> tuple[list[dict], int]:
        data = (props.get("initialState") or {}).get("itemsData")
        if not data:  # talabat redirects past the last page
            return [], 0
        keep = ("title", "slug", "price", "originalPrice", "sku", "stockAmount")
        return [{k: i.get(k) for k in keep} for i in data["items"]], int(data.get("pageCount") or 1)

    async def refresh(self) -> dict:
        """Download the whole branch catalogue and save it to the cache file."""
        async with _download_lock():
            return await self._refresh()

    async def _refresh(self) -> dict:
        store, subs = self.parse_menu(await self._get("menu.json"))
        sem = asyncio.Semaphore(self.concurrency)
        items: dict[str, dict] = {}
        failed: list[str] = []

        async def page(cat: str, sub: str, n: int) -> int:
            async with sem:
                got, pages = self.parse_items(await self._get("grocery-items.json", categorySlug=cat,
                                                              subCategorySlug=sub, page=n))
                await asyncio.sleep(REQUEST_GAP_S)
            for it in got:
                items.setdefault(str(it["sku"]), it)
            return pages

        async def sub_all(cat: str, sub: str) -> None:
            try:
                pages = await page(cat, sub, 1)
                await asyncio.gather(*(page(cat, sub, n) for n in range(2, pages + 1)))
            except Exception as e:  # one broken aisle must not lose the rest
                failed.append(f"{cat}/{sub}")
                log.debug("talabat %s %s/%s failed: %s", self.label, cat, sub, e)

        await asyncio.gather(*(sub_all(c, s) for c, s, _ in subs if c not in self.skip))
        if failed:
            old = self._snapshot or (json.loads(self.cache_path.read_text(encoding="utf-8"))
                                     if self.cache_path.exists() else None)
            for it in (old or {}).get("items", []):  # keep last known prices for aisles that failed
                items.setdefault(str(it["sku"]), it)
            log.warning("%s: %d of %d aisles failed this time (talabat rate limit?); kept older prices "
                        "for those and will retry later.", self.label, len(failed), len(subs))
        snap = {"fetched_at": now_utc().isoformat(), "store": store, "items": list(items.values()),
                "failed": len(failed)}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        log.info("%s snapshot: %d items", self.label, len(items))
        return snap

    def _is_fresh(self, snap: dict | None) -> bool:
        """Fresh = recent and complete. A snapshot with failed aisles is retried (in the background)."""
        return (bool(snap) and not snap.get("failed")
                and now_utc() - datetime.fromisoformat(snap["fetched_at"]) < self.max_age)

    async def snapshot(self, wait: bool = False) -> dict:
        """The cached catalogue. A stale copy is served while a new one downloads in the background.

        With no copy yet, the download starts in the background and a search is told to try
        later, so one shop's first download (minutes for talabat Mart) never holds up a whole
        comparison. ``wait=True`` (start-up warm-up) waits for it instead.
        """
        if self._snapshot is None and self.cache_path.exists():
            self._snapshot = json.loads(self.cache_path.read_text(encoding="utf-8"))
        downloading = self._bg is not None and not self._bg.done()
        if self._snapshot is None:
            if not downloading:
                self._bg = asyncio.create_task(self._refresh_bg())
            if wait:
                await asyncio.shield(self._bg)
                if self._snapshot is not None:
                    return self._snapshot
                raise RuntimeError(f"{self.label}: catalogue download failed; see the log, then retry.")
            raise RuntimeError(f"{self.label}: catalogue still downloading (first run). "
                               "Try again in a few minutes, or run `shopper refresh talabat` once.")
        if not self._is_fresh(self._snapshot) and not downloading:
            self._bg = asyncio.create_task(self._refresh_bg())
        return self._snapshot

    async def _refresh_bg(self) -> None:
        try:
            self._snapshot = await self.refresh()
        except Exception:
            log.exception("talabat background refresh failed; keeping the old snapshot")

    # ---- Adapter -------------------------------------------------------------
    def offers_from(self, snap: dict) -> list[Offer]:
        at = datetime.fromisoformat(snap["fetched_at"])
        base = f"{SITE}/{self.branch_id}/{self.branch_slug}/product"
        out = []
        for it in snap["items"]:
            if it.get("price") is None:
                continue
            reg = it.get("originalPrice")
            out.append(Offer(
                platform=self.name, name=" ".join(it["title"].split()), price=float(it["price"]),
                regular_price=float(reg) if reg else None,
                url=f"{base}/{it['slug']}/s/{it['sku']}?aid={self.area_id}",
                sku=str(it["sku"]), in_stock=(it.get("stockAmount") or 0) > 0, fetched_at=at,
            ))
        return out

    async def search(self, query: str, limit: int = 12) -> list[Offer]:
        want = tokens(query)
        if not want:
            return []
        scored = []
        for o in self.offers_from(await self.snapshot()):
            have = tokens(o.name)
            hit = sum(has_word(w, have) for w in want) / len(want)
            if hit >= 0.6:
                scored.append((hit, o))
        scored.sort(key=lambda x: (-x[0], x[1].price))
        return [o for _, o in scored[: max(limit, 30)]]

    def live_fees(self) -> PlatformFees | None:
        store = (self._snapshot or {}).get("store")
        if not store or store.get("deliveryFee") is None:
            return None
        return PlatformFees(delivery_fee=float(store["deliveryFee"]),
                            min_order=float(store.get("minimumOrderValue") or 0), verified=True)

    async def close(self) -> None:
        if self._bg and not self._bg.done():
            self._bg.cancel()
        if self._client:
            await self._client.aclose()
            self._client = None


async def list_shops(area_id: int, area_slug: str) -> list[dict]:
    """Every grocery shop talabat delivers from to this area (all listing pages)."""
    out: dict[int, dict] = {}
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA}) as http:
        for page in range(1, 20):
            r = await http.get(f"{DATA}/vertical/vertical-area.json", params={
                "countrySlug": "egypt", "vertical": "groceries", "areaId": area_id, "areaSlug": area_slug, "page": page})
            r.raise_for_status()
            vendors = r.json()["pageProps"].get("vendors") or []
            if not vendors:
                break
            for v in vendors:
                out.setdefault(int(v["branchId"]), {
                    "branch_id": int(v["branchId"]), "slug": v["branchSlug"], "name": v["name"],
                    "kind": v.get("cuisineString", ""), "delivery_fee": v.get("deliveryFee"),
                    "min_order": v.get("minimumOrderAmount")})
    return list(out.values())
