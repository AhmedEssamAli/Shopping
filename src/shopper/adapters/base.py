from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Offer


class NotCalibrated(RuntimeError):
    """The adapter has no verified search method yet."""


class Adapter(ABC):
    name: str
    label: str
    needs_browser: bool = False
    login_url: str = ""

    @abstractmethod
    async def search(self, query: str, limit: int = 12) -> list[Offer]: ...

    def cart_link(self, lines: list[tuple[Offer, int]]) -> str | None:
        """One link that puts all lines into this platform's cart, if the platform supports it."""
        return None

    async def close(self) -> None:
        return None
