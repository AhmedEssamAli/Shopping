from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .units import Size, parse_size


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ItemRequest:
    """One line of the shopping list."""
    query: str
    qty: int = 1

    @property
    def size(self) -> Size | None:
        return parse_size(self.query)


@dataclass
class Offer:
    """One product listing on one platform, at the moment it was fetched."""
    platform: str
    name: str
    price: float  # EGP, what you pay now
    url: str
    sku: str = ""
    regular_price: float | None = None
    in_stock: bool = True
    cart_ref: str = ""  # platform id used to build cart links (e.g. Amazon ASIN)
    image: str = ""  # product photo URL, when the shop gives one
    fetched_at: datetime = field(default_factory=now_utc)

    @property
    def size(self) -> Size | None:
        return parse_size(self.name) or parse_size(self.url.rsplit("/", 1)[-1])

    @property
    def on_deal(self) -> bool:
        return self.regular_price is not None and self.price < self.regular_price

    @property
    def unit_price(self) -> float | None:
        """EGP per litre, per kg or per piece."""
        s = self.size
        if not s or not s.total:
            return None
        per = 1 if s.unit == "pcs" else 1000
        return self.price / s.total * per


@dataclass
class Match:
    request: ItemRequest
    offer: Offer
    score: float  # 0-100
    exact: bool  # name covers the request and the size agrees
    pinned: bool = False  # the product you chose for this line before

    @property
    def line_total(self) -> float:
        return self.offer.price * self.request.qty


@dataclass
class PlatformFees:
    delivery_fee: float = 0.0
    service_fee_pct: float = 0.0
    min_order: float = 0.0
    free_delivery_over: float | None = None
    verified: bool = False  # True only after you copy the numbers from a real checkout
    service_fee_min: float = 0.0  # the percentage fee is kept between these (Amazon Now: 4%, EGP 5-10)
    service_fee_max: float | None = None

    def fees_for(self, subtotal: float) -> float:
        delivery = 0.0 if self.free_delivery_over is not None and subtotal >= self.free_delivery_over else self.delivery_fee
        service = subtotal * self.service_fee_pct / 100
        if self.service_fee_pct:
            service = max(service, self.service_fee_min)
            if self.service_fee_max is not None:
                service = min(service, self.service_fee_max)
        return delivery + service


@dataclass
class Proposal:
    platforms: tuple[str, ...]
    assignments: dict[int, Match]  # request index -> chosen match
    subtotals: dict[str, float]
    fees: dict[str, float]
    missing: list[int]  # request indexes with no acceptable match in these platforms

    @property
    def close_count(self) -> int:
        return sum(not m.exact for m in self.assignments.values())

    @property
    def items_total(self) -> float:
        return sum(self.subtotals.values())

    @property
    def fees_total(self) -> float:
        return sum(self.fees.values())

    @property
    def total(self) -> float:
        return self.items_total + self.fees_total
