"""Platforms without a calibrated search method yet.

Each one is listed so `shopper login` and `shopper capture` work today. Once a capture
from the home PC shows the platform's search API, replace the entry with a real adapter.
"""
from __future__ import annotations

from ..models import Offer
from .base import Adapter, NotCalibrated

HOME_URLS = {
    "carrefour": "https://www.carrefouregypt.com/",
    "breadfast": "https://www.breadfast.com",  # app-only: site links to app stores
    "rabbit": "https://www.rabbitmart.com",
}


class PendingAdapter(Adapter):
    needs_browser = True

    def __init__(self, name: str, label: str):
        self.name = name
        self.label = label
        self.login_url = HOME_URLS[name]

    async def search(self, query: str, limit: int = 12) -> list[Offer]:
        raise NotCalibrated(f"{self.label}: not calibrated. Run `shopper capture {self.name}` on the home PC.")
