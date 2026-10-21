# shopper — best basket price across Egyptian shopping platforms

Type one item or a whole list. shopper searches each platform that delivers to New Cairo, matches the exact product and size, and then proposes the cheapest plan: one platform, or a split across platforms, after fees. You get product links, cart links where the platform supports them, and a full comparison table.

It runs on your home PC. You use it from your phone through Telegram. You sign in to each platform **once**, in one browser profile that stays signed in.

## What works today

| Platform | Status | How |
|---|---|---|
| Gourmet Egypt, Seoudi Market, Spinneys Egypt | **live**, tested 2026-10-18 | public Magento GraphQL catalogue, no login |
| **Metro Markets** (own site) | **live**, tested 2026-10-20 | its public search page; delivery to your address not verified |
| HyperOne | built, **off by default** | public Magento catalogue; no New Cairo branch found, so check the app before you enable it |
| **talabat grocery shops**: talabat Mart, Carrefour, Spinneys, Metro, Lulu, Zaher Dairy, The Grove, Zidan (79 shops deliver to Tagammoa 5) | **live**, tested 2026-10-18 | talabat's own data routes, no login; each shop's catalogue is saved daily and searched locally; delivery fee and minimum order come live from talabat |
| noon | **search live on the home PC**, tested 2026-10-19; cart push rebuilt, not yet verified live | Shopping Chrome. Cart push presses Add to Cart on the product page; noon refused shopper's direct cart call (HTTP 403). On a failed search, shopper saves the page to `~/.shopper/captures/noon/failed-search-*`; send that folder |
| Amazon.eg | **search and cart push live on the home PC**, tested 2026-10-19 | Shopping Chrome. Cart push opens each product and presses Add to Cart. Amazon's cart-link form did not add items on .eg, so shopper no longer shows it |
| Instashop shops (Mahmoud ElFar by default) | **search live on the home PC**, tested 2026-10-19 | reads the shop page in Shopping Chrome. shopper types the query, presses Enter and reads the results popup (verified from your dump). The popup has no product links, so offers link to the shop page. The product tiles are verified from your capture. Instashop encrypts its API and requires reCAPTCHA, and shopper does not bypass either |
| **noon Minutes**, **Amazon Now** | built 2026-10-20, **not verified live** | Shopping Chrome, from the links you gave; a failed search saves the page to `~/.shopper/captures/<shop>/failed-search-*` |
| Carrefour direct site | not needed | Carrefour comes through talabat |
| Breadfast, Rabbit | **app shops**, from 2026-10-20 | their websites have no catalogue (checked again 2026-10-20). Send a screenshot of the app's search results to the bot (caption `breadfast` or `rabbit`) or upload it on the home page; Claude reads each product and price. Typing `item = price` lines still works. Comparisons use each price for 30 days and say how old it is |

Every other shop that was checked, Amazon Now and noon Minutes included, is in [docs/retailers.md](docs/retailers.md), with the next step for each.

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

## How to write an item

An item has six fields. Only the item is required. Every field you fill must match; every field you leave empty is open.

| Field | Means | Examples |
|---|---|---|
| Item | what it is | milk, rice, instant coffee |
| Brand | only this brand counts as exact; empty = any brand | Juhayna, Al Doha |
| Type | variant, flavour or grade | skimmed, full cream, basmati |
| Size of one | one piece | 1L, 200ml, 5kg |
| Pieces in pack | pieces in one pack | 6 (a 6-pack), 30 (eggs) |
| How many | packs to buy; empty = 1 | 2 |

**How a typed line is split** (also on the home page, under "How shopper reads a typed line"):

- **Exact way:** `item | brand | type | size | pieces | how many`, for example `milk | Juhayna | skimmed | 1L | 6 | 2`. Read by position; nothing is guessed; an empty field stays open.
- **Free way**, in this order: how many (a number at the start or end that is not a size: `2 milk`, `milk x2`); size (`1L`, `200 ml`, `5 kg`); pieces (`6x1L`, `pack of 6`, `30 pcs`, `30 eggs`); brand (a known brand); type (a known type word); item (everything left). With `ANTHROPIC_API_KEY`, Claude fills the same fields for Arabic and free text.

Typed and pasted lines use the same order: how many, brand, type, item, pieces x size. `2 Juhayna skimmed milk 6x1L` is two six-packs of 1 L Juhayna skimmed milk. The home page has one row of fields per item with a live "Searches for" line; Telegram's **Add an item** asks the fields one by one, with buttons for brands, types and sizes seen in shops before. Result pages, the list and the bot show how each line was read.

## The home page

Open `WEB_BASE_URL/?t=<WEB_TOKEN>`. It shows:
- A step bar: **1 Add items · 2 Review · 3 Compare · 4 Order**.
- Item rows (see above) with − / + quantity, and a box to paste a list. Rows compare at once; a pasted list, or **Compare the list**, first opens **Check your list**: every line as editable fields, with Remove, **Compare N items** and **Save as my list**. The item field suggests lines you used before.
- **Products you chose before** as chips: tap one to put that line in the box.
- **The household list**, with − / + per line, Remove, **Compare the list**, **Clear the list**, and **Keep a copy as** a named list.
- **Saved lists** ("Weekly", "Monthly"): **Review and compare**, **Make it my list**, Delete. Recent comparisons have **Buy again**.
- **Recent comparisons**, each with its item count and best total. A page from before the last restart opens as history, without buttons.
- **Your delivery area**: Seoudi's own list of districts in New Cairo, each with the branch that serves it. Choose yours once; Seoudi prices and stock then come from that branch. A district Seoudi lists without a branch (for example South El-Lotus, 2026-10-20) uses its default store, and the result page says so.
- **Prices from the apps**: typed lines or a screenshot upload for Breadfast and Rabbit.
- **Shops**, grouped by how shopper reaches them.

If `WEB_TOKEN` is empty, `shopper serve` creates an access key, saves it in `~/.shopper/web_token`, and prints it, so the page is never open to anyone on your network.

## Install as a phone app

The web page installs as an app on your phone's home screen. It opens full screen, with its own icon. The PC still does the work, so it must be on and running `shopper serve`. When the PC is off, the app shows "shopper is offline" instead of a browser error.

1. Finish the Tailscale step above, so the page has an `https://<pc>.<tailnet>.ts.net` address. An app installs only over https.
2. On the phone, open the "Full table" link from any bot reply, or `WEB_BASE_URL/?t=<WEB_TOKEN>`.
3. Android (Chrome): menu ⋮ → **Install app** (or **Add to Home screen**). iPhone (Safari): Share → **Add to Home Screen**.

In the app you paste a list, see the comparison, and press **Add the best plan to my carts** (noon and Amazon). The other platforms keep their product links.

## The comparison page

- **1 shop / Up to 2 shops / Up to 3 shops** above the plan, each with its best total. Tap one to re-plan.
- The best plan is a set of numbered **order cards**, largest first: each shop's lines, subtotal, fees and one action, **Add N items to cart** (noon, Amazon) or **Copy list and open the shop**, then **Done: order placed**. The plan shows "1 of 2 orders placed".
- **Filters** above the plan: shops (supermarkets, quick delivery, marketplaces, app shops), brands found, exact matches only. Applying re-plans without a search.
- Each line shows its fields as chips; tap them (**Adjust search**) to change item, brand, type, size or pack and **Search again for this item**: only that line is searched again. A line no shop had opens this form.
- **Which one do you mean?** shows one product per card (one brand, size, pack and variant) with the shops that sell it.
- **If you buy everything in one shop** is a table: total with fees, items found and exact, fees, difference from the plan, missing items, and **Use only this shop**. Notes fold into one line.
- Each line has − / + and **Remove line** (with Undo). A change re-plans from the prices already fetched, in milliseconds, without searching the shops again. **Refresh prices** appears after 30 minutes.
- `shopper stats` reads the measures from your own use: time to check a list, quantity changes, split plans fully ordered.

Below the plan, each item shows one shelf label per shop, cheapest first; the cheapest exact match has a green border. Close matches have a dashed label. **If you buy everything in one shop** shows what the whole basket costs at each shop on its own; shops that miss items get a striped bar. It follows your phone's dark mode.

## Use

Telegram:
- Send one line to compare that item now: `2 Juhayna full cream milk 1L`. Send several lines to compare a basket.
- After a split plan, the bot sends one message per order with **Add to cart**, **Open the shop** and **Done**. `/lists` shows saved lists; `/lists save Weekly` keeps a copy of the household list; `/list` has − / + per line.
- The keyboard under the text box has **Compare my list**, **My list**, **Add an item**, **My watches** and **How to use**. The "/" button lists every command.
- Under each result: **Add the plan to my carts** (shown once the plan is final; it works once per result), **Save as my list**, and **Open the full table** (when `WEB_BASE_URL` is your https Tailscale address).
- **The household list** is shared by everyone in `TELEGRAM_ALLOWED_CHAT_IDS` and by the web page. `/add <lines>` adds; under `/list`, **Remove**, **Compare the list** and **Clear the list** are buttons.
- `/watch Nescafe classic 200g @ 250` watches an item. The bot re-prices it every `WATCH_INTERVAL_HOURS` (default 6). With a target, it alerts you when the cheapest exact match reaches it; without one, when the price drops by more than 1%. Alerts carry **Open the product** and **Stop watching** buttons; `/watches` lists them with **Stop** buttons.
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

## Instashop shops

Instashop, like talabat, has many shops. Run `shopper instashop-shops` with Shopping Chrome open and your address set on Instashop: it prints one config line per shop it shows you. Paste the ones you use into `[instashop] shops` in `config/platforms.toml`. Each is searched in Shopping Chrome, so keep it to a few. If a search fails, the error says so and saves the page.

## talabat shops

`shopper talabat-shops` lists every grocery shop talabat delivers from to your area, with its fee and minimum order, and prints a ready line for each. Paste lines into `shops = [...]` under `[talabat]` in `config/platforms.toml` to compare more shops. Each shop's catalogue (1,000 to 9,000 items) downloads in the background when `shopper serve` starts and again once a day, so searches stay instant. `shopper refresh talabat` forces it now. On a fresh install, run it once before your first search. It downloads one shop at a time, politely: about 10–15 minutes for eight shops. While a shop is still downloading, searches skip it and say so.

## How it decides

1. **Match.** An offer is an *exact* match when it contains every word you typed (accents and spelling slips allowed), its size agrees (`6*1l`, `Pack of 6`, `1.5 L` and `200gm` are all parsed), and it carries no variant word you did not ask for (chocolate, lactose free, skimmed, salt & vinegar, ...). Anything else that is close is labelled *close match*.
2. **Rank.** The plan with the fewest missing items wins first, then the plan with the fewest close matches, then the lowest total (items plus fees), then the plan with the fewest orders. A cheaper basket of the wrong sizes is not a saving.
3. **Split.** Every combination of up to `MAX_ORDERS` platforms (default 2) is checked against fees, minimum orders and free-delivery thresholds.

Every price carries the time it was fetched. Always check the total in the platform's app before you pay. shopper never checks out or pays.

## Choose once, shopper remembers

A short line like "Nescafe classic 200g" fits several products: a jar, a pouch and a sachet are all Nescafe Classic 200 g. shopper no longer picks one for you without saying so.

1. **Cards.** The comparison page groups what it found into one card per distinct product: size, pack type (jar, pouch, sachet, ...) and any variant word. Each card lists the platforms that sell it and the price per kg or L. When more than one card fits, the plan says **Not final** and the line says **choose your product**.
2. **This one.** Press it on the card you mean. The plan is recomputed at once, from the prices already fetched, with no new search. Next time you send the same line, in any spelling ("NESCAFÉ Classic 200 gm" counts as the same), that product wins on the platforms where you chose it. Other platforms are searched and matched with your choice added ("... 200g jar"), so a pouch no longer counts as your jar.
3. **not this.** Press it under any price in the table. That listing never comes back for that line.
4. **change.** Press it next to "your product" to choose again.
5. **In Telegram.** When several products fit a line, the bot sends "Which one do you mean?" with one button per product (size, pack type, lowest price, shops). Tap one; the bot confirms, offers **Choose again**, and sends the updated plan once every line is chosen.
6. **Words you avoid.** When you pass over a pack or variant word twice and never choose it (for example "sachet"), it stops counting as an exact match on every line that does not ask for it. Choosing it once undoes this.

**Right product? Yes / No.** Every line of a result asks it, on the page and in Telegram (one message with a Yes and a No per line). Yes pins that product, like **This one**. No removes it, like **not this**, and shows the next best.

**The learner** (`learner.py`) turns every answer, pick and "not this" into a labelled example and updates a small logistic-regression model at once, from features such as word coverage, size agreement, variant words, brand and price against the cheapest. Each line shows its **match confidence** (high, medium, low), so you see where the search may have missed. After 20 answers, the learner also drops candidates it rates as probably wrong (below 25%) before the cheapest exact match is chosen. The examples are kept in the SQLite file, so the model can be retrained or checked later.

Why both memory and a small model: memory is exact for the lines you have answered; the model generalises what it learned to new lines. Big retailers train on millions of clicks; one household makes a few dozen answers a month, so the model stays small and starts from sensible default weights. shopper still uses each retailer's own search to find the listings.

The buttons work on comparisons made since `shopper serve` last started. Older pages say "Compare again".

## Adding a pending platform

Run this on the home PC:
```
shopper capture carrefour
```
Use any name from `shopper platforms`. A window opens. Search for one item, open a product, and add it to the cart, then press Enter. Every JSON response and the page HTML are saved to `~/.shopper/captures/<platform>/<time>/`. These show the platform's own search and cart API, and an adapter can then be written against real data, like `src/shopper/adapters/magento.py`. Captures can contain session tokens: check before you share one, and delete the cookies/authorization values first.

## Layout

```
src/shopper/
  adapters/        magento.py (Gourmet, Seoudi, Spinneys, HyperOne) · metro.py · talabat.py (any talabat shop)
                   noon.py · amazon_eg.py · instashop.py · pending.py
  units.py         size and pack parsing
  matching.py      exact / close match rules
  memory.py        your picks, rejects and avoided words
  optimizer.py     landed-cost basket split
  service.py       parallel search, match, optimise
  parsing.py       list parsing (local, or Claude for Arabic and free text)
  browser.py       persistent profile, login, capture
  report.py        Telegram text and HTML table
  watcher.py       price watches and alerts
  bot.py · web.py · pwa.py (phone app) · cli.py · store.py (SQLite price history)
tests/             172 tests; Magento fixtures are real responses captured 2026-10-18
```

Run the tests with `pip install -e ".[dev]" && pytest`.
