# shopper — best basket price across Egyptian shopping platforms

Type one item or a whole list. shopper searches each platform that delivers to New Cairo, matches the exact product and size, and then proposes the cheapest plan: one platform, or a split across platforms, after fees. You get product links, cart links where the platform supports them, and a full comparison table.

It runs on your home PC. You use it from your phone through Telegram. You sign in to each platform **once**, in one browser profile that stays signed in.

## What works today

| Platform | Status | How |
|---|---|---|
| Gourmet Egypt, Seoudi Market, Spinneys Egypt | **live**, tested 2026-10-18 | public Magento GraphQL catalogue, no login |
| **talabat grocery shops**: talabat Mart, Carrefour, Spinneys, Metro, Lulu, Zaher Dairy, The Grove, Zidan (79 shops deliver to Tagammoa 5) | **live**, tested 2026-10-18 | talabat's own data routes, no login; each shop's catalogue is saved daily and searched locally; delivery fee and minimum order come live from talabat |
| noon | **built, search not yet working** | Shopping Chrome. On a failed search, shopper saves the page to `~/.shopper/captures/noon/failed-search-*`; send that folder |
| Amazon.eg | **search live on the home PC**; cart push built, not yet verified live | Shopping Chrome. Cart push opens each product and presses Add to Cart. Amazon's cart-link form did not add items on .eg, so shopper no longer shows it |
| Instashop shops (Mahmoud ElFar by default) | **built, search not verified live** | reads the shop page in Shopping Chrome. The search box is a type-ahead: shopper types the query and opens the best suggestion. The product tiles are verified from your capture. Instashop encrypts its API and requires reCAPTCHA, and shopper does not bypass either |
| Carrefour direct site | not needed | Carrefour comes through talabat |
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
   py -m venv .venv                # Windows (py launcher); macOS: python3 -m venv .venv
   .venv\Scripts\activate          # PowerShell: .venv\Scripts\Activate.ps1; macOS: source .venv/bin/activate
   python -m pip install -e .      # inside the venv, `python` works on Windows too
   ```
3. Copy `.env.example` to `.env`.
4. Check that it works with the live platforms. No login is needed:
   ```
   shopper search "Juhayna skimmed milk 1L" --platforms gourmet,seoudi,spinneys
   ```
5. Open **Shopping Chrome** and sign in once. Only noon and Amazon need it; talabat and the grocers need no login.
   - Run `scripts\windows\start-shopping-chrome.cmd`. A normal Chrome window opens with its own profile, `%LOCALAPPDATA%\shopper-chrome`.
   - In that window, sign in to noon and Amazon.eg (OTP) and set your New Cairo address. Browse there as usual.
   - Keep it open; minimised is fine. shopper attaches to it (`BROWSER_CDP_URL` in `.env`) and only opens and closes background tabs.
   - Why a separate profile: Chrome 136 and later block this kind of attach on your default profile. This window behaves like everyday Chrome, without the "automated software" mode that triggers bot checks.
   - Check it with `shopper browser-check`. It reports the Chrome version, the tab count and the attach time.
   - To have shopper launch its own browser instead, empty `BROWSER_CDP_URL` and use `shopper login noon`.
6. Enter real fees. Copy the delivery fee, service fee and minimum order from one checkout per platform into `config/platforms.toml`, then set `verified = true`. Until you do, fees count as 0 and every result says "fees not verified".
7. Set up the phone link:
   - **Bot.** In Telegram, open @BotFather, send `/newbot`, and pick a name and a username that ends in `bot`. Put the token it returns in `TELEGRAM_BOT_TOKEN`.
   - **Your chat ID.** Run `shopper serve` and send `/start` to your bot. It replies with your chat ID. Stop the server with Ctrl+C and put the ID in `TELEGRAM_ALLOWED_CHAT_IDS`. Add family members with commas.
   - **Web token.** Set `WEB_TOKEN` to the output of `python -c "import secrets;print(secrets.token_urlsafe(16))"`.
   - **Tailscale** (needed only for the "Full table" link). Install it on the PC and on the phone, signed in with the same account. On the PC, run `tailscale serve --bg 8765`, then set `WEB_BASE_URL` to the `https://<pc>.<tailnet>.ts.net` address it prints. Keep `WEB_HOST=127.0.0.1`: Tailscale forwards to it, so nothing is exposed to the internet.
   - Start `shopper serve` again. Keep the PC awake while you shop.

## Use

Telegram:
- Send one line to compare that item now: `2 Juhayna full cream milk 1L`.
- Send several lines to compare that basket now.
- `/add <lines>`, `/list`, `/remove 3` and `/clear` keep a running list. `/compare` (or `/compare exact`) compares the saved list.
- `/watch Nescafe classic 200g @ 250` watches an item. The bot re-prices it every `WATCH_INTERVAL_HOURS` (default 6). It alerts you when the cheapest exact match reaches your price, or drops by more than 1% when no target is set. `/watches` lists them and `/unwatch 3` removes one.
- Each comparison also lists **better-value packs**: the same product in another size that costs at least 5% less per litre, kg or piece. Example: a 6 × 1 L pack against single litres.
- `/push` adds the last best plan to your carts where the platform allows it (noon and Amazon today); other platforms get links.
- Arabic and free text work when `ANTHROPIC_API_KEY` is set. Claude turns `2 لبن جهينة كامل الدسم لتر` into `2 × Juhayna full cream milk 1L`. Without the key, a local parser reads one item per line.

CLI:
```
shopper basket -f list.txt --html table.html      # one item per line
shopper basket "milk 1L x2, rice 5kg" --exact --max-orders 1
shopper health                                     # live checks + browser session cookies
shopper watch-check                                # re-price all watches now
shopper basket -f list.txt --debug                 # show each platform's top candidates and why
shopper basket -f list.txt --push                  # also add the best plan to your noon and Amazon carts
shopper platforms
```

## Start automatically on Windows

Run `scripts\windows\install-autostart.cmd` once. Also put a shortcut to `scripts\windows\start-shopping-chrome.cmd` in your Startup folder (`shell:startup`) so Shopping Chrome opens at logon. It creates a scheduled task that starts `shopper serve` at every logon and logs to `%USERPROFILE%\.shopper\serve.log`. Start it now with `schtasks /Run /TN "shopper"`. Remove it with `schtasks /Delete /TN "shopper" /F`.

## talabat shops

`shopper talabat-shops` lists every grocery shop talabat delivers from to your area, with its fee and minimum order, and prints a ready line for each. Paste lines into `shops = [...]` under `[talabat]` in `config/platforms.toml` to compare more shops. Each shop's catalogue (1,000 to 9,000 items) downloads in the background when `shopper serve` starts and again once a day, so searches stay instant. `shopper refresh talabat` forces it now. On a fresh install, run it once before your first search. It downloads one shop at a time, politely: about 10–15 minutes for eight shops. While a shop is still downloading, searches skip it and say so.

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
  adapters/        magento.py (Gourmet, Seoudi, Spinneys) · talabat.py (any talabat shop) · noon.py · amazon_eg.py · pending.py
  units.py         size and pack parsing
  matching.py      exact / close match rules
  optimizer.py     landed-cost basket split
  service.py       parallel search, match, optimise
  parsing.py       list parsing (local, or Claude for Arabic and free text)
  browser.py       persistent profile, login, capture
  report.py        Telegram text and HTML table
  watcher.py       price watches and alerts
  bot.py · web.py · cli.py · store.py (SQLite price history)
tests/             88 tests; Magento fixtures are real responses captured 2026-10-18
```

Run the tests with `pip install -e ".[dev]" && pytest`.
