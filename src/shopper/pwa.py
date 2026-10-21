"""Installable phone app (PWA) for the web page: manifest, icons and an offline notice.

Installed from the phone browser ("Add to Home screen" / "Install app") over the
Tailscale https address, it opens full screen like an app. The work still runs on the
home PC; when the PC is off, the app shows a short notice instead of a browser error.
"""
from __future__ import annotations

import json
import struct
from urllib.parse import quote
import zlib
from functools import lru_cache

THEME = "#16A34A"

SERVICE_WORKER = """const OFFLINE = '/offline';
self.addEventListener('install', e => e.waitUntil(caches.open('shopper').then(c => c.add(OFFLINE)).then(() => self.skipWaiting())));
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
// Network only: prices must be live. Navigations that fail get the offline notice.
self.addEventListener('fetch', e => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).catch(() => caches.match(OFFLINE)));
});
"""

OFFLINE_BODY = ("<h1>shopper is offline</h1><p class=muted>The home PC did not answer. Check that it is on, "
                "awake and signed in to Tailscale, and that <code>shopper serve</code> is running.</p>"
                "<p><button onclick='location.reload()'>Retry</button></p>")


def manifest(token: str | None) -> str:
    start = f"/?t={token}" if token else "/"
    return json.dumps({
        "name": "shopper", "short_name": "shopper", "description": "Best basket price in New Cairo",
        "start_url": start, "scope": "/", "display": "standalone",
        "background_color": "#F8FAFC", "theme_color": THEME,
        "icons": [{"src": f"/icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any maskable"}
                  for n in (192, 512)],
    })


def head_tags(token: str | None) -> str:
    q = f"?t={quote(token)}" if token else ""
    return (f"<link rel=manifest href='/manifest.webmanifest{q}'><meta name=theme-color content='{THEME}'>"
            "<link rel=apple-touch-icon href='/icon-192.png'><meta name=apple-mobile-web-app-capable content=yes>"
            "<meta name=apple-mobile-web-app-title content=shopper>"
            "<script>if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js')</script>")


def add_head(html: str, token: str | None) -> str:
    return html.replace("</head>", head_tags(token) + "</head>", 1)


@lru_cache(maxsize=4)
def icon_png(size: int) -> bytes:
    """A white cart on green, drawn in code so the repo carries no binary files."""
    s = size / 100  # draw on a 100-unit grid; content stays inside the maskable safe zone

    def inside(x: float, y: float) -> bool:
        x, y = x / s, y / s
        basket = 30 <= y <= 56 and 28 + (y - 30) * 0.25 <= x <= 74 - (y - 30) * 0.25
        handle = 22 <= x <= 32 and 26 <= y <= 31
        wheels = any((x - cx) ** 2 + (y - 66) ** 2 <= 25 for cx in (40, 62))
        return basket or handle or wheels

    green, white = bytes((0x16, 0xA3, 0x4A)), bytes((255, 255, 255))
    rows = b"".join(b"\x00" + b"".join(white if inside(x + 0.5, y + 0.5) else green for x in range(size))
                    for y in range(size))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))
