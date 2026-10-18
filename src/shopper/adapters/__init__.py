from __future__ import annotations

from ..browser import BrowserSession
from ..config import Settings
from .amazon_eg import AmazonEgAdapter
from .base import Adapter, NotCalibrated
from .magento import gourmet, seoudi, spinneys
from .pending import HOME_URLS, PendingAdapter


def build_adapters(settings: Settings, session: BrowserSession | None) -> dict[str, Adapter]:
    """Every platform in config/platforms.toml, live or not."""
    out: dict[str, Adapter] = {"gourmet": gourmet(), "seoudi": seoudi(), "spinneys": spinneys()}
    if session is not None:
        out["amazon_eg"] = AmazonEgAdapter(session)
    for name in HOME_URLS:
        label = settings.platforms[name].label if name in settings.platforms else name
        out[name] = PendingAdapter(name, label)
    return out


__all__ = ["Adapter", "NotCalibrated", "build_adapters", "AmazonEgAdapter"]
