# shopper — best basket price across Egyptian shopping platforms

Type one item or a whole list. shopper searches each platform that delivers to New Cairo, matches the exact product and size, and then proposes the cheapest plan: one platform, or a split across platforms, after fees. You get product links, cart links where the platform supports them, and a full comparison table.

It runs on your home PC. You use it from your phone through Telegram. You sign in to each platform **once**, in one browser profile that stays signed in.

## What works today

| Platform | Status | How |
|---|---|---|
| Gourmet Egypt | **live**, tested 2026-10-18 | public Magento GraphQL catalogue, no login |
| Seoudi Market | **live**, tested 2026-10-18 | public Magento GraphQL catalogue, no login |
| Spinneys Egypt | **live**, tested 2026-10-18 | public Magento GraphQL catalogue, no login |
| Amazon.eg | **built, not verified live** | signed-in browser profile; cloud IPs are blocked, so verify on the home PC |
| talabat, noon, Carrefour, Instashop (incl. Mahmoud ElFar) | **pending** | they block cloud IPs; calibrate from a capture on the home PC (see below) |
| Breadfast, Rabbit | **pending** | Breadfast's website has only app-store links; Rabbit is app-first |

## Why it runs at home, not in the cloud

- From a cloud server, Amazon.eg returned a bot check, and talabat, noon, Carrefour and Instashop refused or dropped the connection.
- Your prices depend on your delivery address and branch. Only your own signed-in session sees your real New Cairo price.
- Your sessions stay on your machine. The tool never stores a password.

## Setup (home PC, Windows or macOS)

1. Install Python 3.11+ and Google Chrome. Without Chrome, run `playwright install chromium` after step 2.
2. Install the project:
   ```
   git clone https://github.com/AhmedEssamAli/Shopping.git && cd Shopping
   python -m venv .venv
   .venv\Scripts\activate          # macOS: source .venv/bin/activate
   pip install -e .
   ```
3. Copy `.env.example` to `.env`.
4. Check that it works with the live platforms. No login is needed:
   ```
   shopper search "Juhayna skimmed milk 1L" --platforms gourmet,seoudi,spinneys
   ```
5. Sign in once per browser platform. A window opens; you sign in (OTP) and set your New Cairo address:
   ```
   shopper login amazon_eg
   ```
6. Enter real fees. Copy the delivery fee, service fee and minimum order from one checkout per platform into `config/platforms.toml`, then set `verified = true`. Until you do, fees count as 0 and every result says "fees not verified".
7. Set up the phone link:
   - Create a bot with @BotFather and put the token in `TELEGRAM_BOT_TOKEN`.
   - Run `shopper serve` and send `/start` to the bot. It replies with your chat ID. Put that ID in `TELEGRAM_ALLOWED_CHAT_IDS`.
   - Set `WEB_TOKEN` to a random string.
   - Install Tailscale on the PC and on the phone. Set `WEB_BASE_URL` to the PC's Tailscale address, for example `http://home-pc.tailxyz.ts.net:8765`.
   - Restart `shopper serve`.

## Use

Telegram:
- Send one line to compare that item now: `2 Juhayna full cream milk 1L`.
- Send several lines to compare that basket now.
- `/add <lines>`, `/list`, `/remove 3` and `/clear` keep a running list. `/compare` (or `/compare exact`) compares the saved list.
- Arabic and free text work when `ANTHROPIC_API_KEY` is set. Claude turns `2 لبن جهينة كامل الدسم لتر` into `2 × Juhayna full cream milk 1L`. Without the key, a local parser reads one item per line.

CLI:
```
shopper basket -f list.txt --html table.html      # one item per line
shopper basket "milk 1L x2, rice 5kg" --exact --max-orders 1
shopper health                                     # live checks + browser session cookies
shopper platforms
```

## How it decides

1. **Match.** An offer is an *exact* match when it contains every word you typed (accents and spelling slips allowed), its size agrees (`6*1l`, `Pack of 6`, `1.5 L` and `200gm` are all parsed), and it carries no variant word you did not ask for (chocolate, lactose free, skimmed, salt & vinegar, ...). Anything else that is close is labelled *close match*.
2. **Rank.** The plan with the fewest missing items wins first, then the plan with the fewest close matches, then the lowest total (items plus fees), then the plan with the fewest orders. A cheaper basket of the wrong sizes is not a saving.
3. **Split.** Every combination of up to `MAX_ORDERS` platforms (default 2) is checked against fees, minimum orders and free-delivery thresholds.

Every price carries the time it was fetched. Always check the total in the platform's app before you pay. shopper never checks out or pays.

## Adding a pending platform

Run this on the home PC:
```
shopper capture talabat
```
A window opens. Search for one item, open a product, and add it to the cart, then press Enter. Every JSON response and the page HTML are saved to `~/.shopper/captures/talabat/<time>/`. These show the platform's own search and cart API, and an adapter can then be written against real data, like `src/shopper/adapters/magento.py`. Captures can contain session tokens: check before you share one, and delete the cookies/authorization values first.

## Layout

```
src/shopper/
  adapters/        magento.py (Gourmet, Seoudi, Spinneys) · amazon_eg.py · pending.py
  units.py         size and pack parsing
  matching.py      exact / close match rules
  optimizer.py     landed-cost basket split
  service.py       parallel search, match, optimise
  parsing.py       list parsing (local, or Claude for Arabic and free text)
  browser.py       persistent profile, login, capture
  report.py        Telegram text and HTML table
  bot.py · web.py · cli.py · store.py (SQLite price history)
tests/             58 tests; Magento fixtures are real responses captured 2026-10-18
```

Run the tests with `pip install -e ".[dev]" && pytest`.
