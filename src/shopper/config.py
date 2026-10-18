from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .models import PlatformFees

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class PlatformConfig:
    name: str
    label: str
    status: str  # live | browser | pending
    enabled: bool
    fees: PlatformFees


@dataclass
class Settings:
    home: Path
    platforms: dict[str, PlatformConfig]
    area_name: str = "New Cairo / 5th Settlement"
    area_lat: float = 30.0074
    area_lon: float = 31.4913
    max_orders: int = 2
    headless: bool = True
    telegram_token: str = ""
    allowed_chat_ids: set[int] = field(default_factory=set)
    anthropic_key: str = ""
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    web_base_url: str = "http://127.0.0.1:8765"
    web_token: str = ""
    watch_interval_hours: float = 6.0

    @property
    def profile_dir(self) -> Path:
        return self.home / "profile"

    @property
    def captures_dir(self) -> Path:
        return self.home / "captures"

    @property
    def db_path(self) -> Path:
        return self.home / "shopper.sqlite3"

    def enabled(self) -> list[PlatformConfig]:
        return [p for p in self.platforms.values() if p.enabled and p.status != "pending"]

    def fees(self) -> dict[str, PlatformFees]:
        return {n: p.fees for n, p in self.platforms.items()}


def load_platforms(path: Path) -> dict[str, PlatformConfig]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out = {}
    for name, d in data.items():
        fees = PlatformFees(
            delivery_fee=float(d.get("delivery_fee", 0)),
            service_fee_pct=float(d.get("service_fee_pct", 0)),
            min_order=float(d.get("min_order", 0)),
            free_delivery_over=d.get("free_delivery_over"),
            verified=bool(d.get("verified", False)),
        )
        out[name] = PlatformConfig(name, d.get("label", name), d.get("status", "pending"), bool(d.get("enabled", False)), fees)
    return out


def load_settings(platforms_file: Path | None = None) -> Settings:
    load_dotenv(REPO_ROOT / ".env")
    env = os.environ.get
    home = Path(env("SHOPPER_HOME", "~/.shopper")).expanduser()
    ids = {int(x) for x in env("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x}
    return Settings(
        home=home,
        platforms=load_platforms(platforms_file or REPO_ROOT / "config" / "platforms.toml"),
        area_name=env("AREA_NAME", "New Cairo / 5th Settlement"),
        area_lat=float(env("AREA_LAT", "30.0074")),
        area_lon=float(env("AREA_LON", "31.4913")),
        max_orders=int(env("MAX_ORDERS", "2")),
        headless=env("BROWSER_HEADLESS", "1") != "0",
        telegram_token=env("TELEGRAM_BOT_TOKEN", ""),
        allowed_chat_ids=ids,
        anthropic_key=env("ANTHROPIC_API_KEY", ""),
        web_host=env("WEB_HOST", "127.0.0.1"),
        web_port=int(env("WEB_PORT", "8765")),
        web_base_url=env("WEB_BASE_URL", "http://127.0.0.1:8765").rstrip("/"),
        web_token=env("WEB_TOKEN", ""),
        watch_interval_hours=float(env("WATCH_INTERVAL_HOURS", "6")),
    )
