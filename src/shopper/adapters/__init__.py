from __future__ import annotations

from ..browser import BrowserSession
from ..config import Settings
from .amazon_eg import AmazonEgAdapter
from .appshop import AppShopAdapter, PriceBook
from .base import Adapter, NotCalibrated
from .instashop import InstashopAdapter
from .magento import gourmet, hyperone, seoudi, spinneys
from .metro import MetroAdapter
from .noon import NoonAdapter
from .quick import amazon_now, noon_minutes
from .pending import HOME_URLS, PendingAdapter
from .talabat import TalabatShopAdapter, list_shops


def talabat_shops(settings: Settings) -> dict[str, TalabatShopAdapter]:
    out = {}
    for name, p in settings.platforms.items():
        o = p.options
        if not o.get("talabat_branch"):
            continue
        out[name] = TalabatShopAdapter(
            name=name, label=p.label, area_id=int(o["area_id"]), branch_id=int(o["branch_id"]),
            branch_slug=o["slug"], cache_dir=settings.home / "cache",
            max_age_hours=float(o.get("max_age_hours", 24)), skip_categories=tuple(o.get("skip_categories", ())))
    return out


def build_adapters(settings: Settings, session: BrowserSession | None, book: PriceBook | None = None) -> dict[str, Adapter]:
    """Every platform in config/platforms.toml, live or not. ``book``: prices typed in from apps."""
    at = (settings.area_lat, settings.area_lon)
    out: dict[str, Adapter] = {"gourmet": gourmet(), "seoudi": seoudi(at, (settings.platforms.get("seoudi").options or {}).get("area", "") if settings.platforms.get("seoudi") else ""), "spinneys": spinneys(at),
                               "hyperone": hyperone(), "metro_markets": MetroAdapter(), **talabat_shops(settings)}
    if session is not None:
        out["amazon_eg"] = AmazonEgAdapter(session)
        out["noon"] = NoonAdapter(session)
        out["noon_minutes"] = noon_minutes(session)
        out["amazon_now"] = amazon_now(session)
        for name, p in settings.platforms.items():
            if p.options.get("instashop_shop"):
                out[name] = InstashopAdapter(name, p.label, p.options["slug"], session)
    if book is not None:
        for name, p in settings.platforms.items():
            if p.status == "app":
                out[name] = AppShopAdapter(name, p.label, book)
    for name in HOME_URLS:
        label = settings.platforms[name].label if name in settings.platforms else name
        out[name] = PendingAdapter(name, label)
    return out


__all__ = ["Adapter", "NotCalibrated", "build_adapters", "AmazonEgAdapter", "NoonAdapter", "InstashopAdapter", "TalabatShopAdapter", "list_shops"]
