# Plan: "Basket" — personal price-comparison and cart agent (Egypt, New Cairo)

## Context

Ahmed wants one tool. He adds one item or a bulk list (groceries, food, clothes, anything). The tool searches every platform that delivers to New Cairo / 5th Settlement. It returns accurate prices, comparison links and ready-to-buy cart links. For bulk lists it proposes the cheapest plan: one platform, or a split across several. He wants to sign in to each platform once, not every time.

Decisions confirmed by Ahmed:
- Audience: him and his household only. This is not a public product.
- Runtime: a home laptop/PC holds the signed-in browser. He uses it from his phone.
- Area: New Cairo / 5th Settlement.
- Interface: a Telegram bot, plus a web comparison table.

The repo `AhmedEssamAli/Shopping` is empty. This is a greenfield build on branch `claude/festive-mayer-zmadjk`.

## Brainstorm summary (thinking that shaped the plan)

**JTBD.** "When I need to restock or buy something, I want the lowest total cost to my door without opening 10 apps, so I save money and time."

**Challenged premises**
1. *"Accurate pricing everywhere."* Most platforms have no public API. Prices depend on location and dark store (talabat Mart, Rabbit and Breadfast price by branch). Only a session that holds your New Cairo address sees your real price. **Consequence:** the engine must run inside your own logged-in sessions. A generic cloud scraper sees the wrong prices, and gets blocked. Evidence from this session: from a cloud IP, Amazon.eg returned a 202 bot challenge, Instashop returned 403, and noon timed out.
2. *"All platforms."* Breadfast's website contains only app-store links. Rabbit is app-first (reported; not verified). Neither has a web cart to automate, so in v1 they are **out**. Phase 4 evaluates an Android-emulator route. Mahmoud ElFar is reachable through Instashop (its listing names ElFar). Oscar has no web store I could verify (unknown).
3. *"Cheapest item price wins."* The real objective is **total landed cost**: item prices + delivery fee + service fee + minimum-order penalty + promos, minus subscription perks (talabat Pro, noon One, Prime). A split order can lose to one order after two delivery fees. The optimizer must model fees, not just prices.
4. *"Sign in only once."* No single sign-on covers these platforms. Most use phone OTP. The practical equivalent is **one persistent browser profile** on your PC: you log in once per platform, and the cookies persist. The tool never stores passwords. When a session expires (expiry period per platform: unknown), the bot tells you to re-run login for that platform.
5. *"Matching is easy."* It is not. "Juhayna full cream milk 1L" against six near-variants is the hardest accuracy problem. The plan uses LLM parsing into a structured spec, then scored matching, a unit-price check, and an "exact / close substitute" label.

**Options considered (strongest only)**

| Option | Time to value | Accuracy | Sign-in once | Risk | Verdict |
|---|---|---|---|---|---|
| A. Local Python agent + persistent Playwright profile + Telegram bot | 2–3 wks MVP | High (your address, your session) | Yes | Adapters break when sites change | **Recommended** |
| B. Chrome extension side panel | 2–3 wks | High | Yes | Desktop only; no phone UX; JS not Python | Second |
| C. Cloud scraper + bot | 3–4 wks | Low (wrong location, blocked IPs) | No (OTP on server, credential custody) | High | Reject |
| D. Use Claude in Chrome ad hoc, no build | 0 days | Medium | Yes | Slow and costly per basket | Use only for the Phase 0 spike |

**Riskiest assumption.** Scripted web prices in a New Cairo session equal the app's prices and checkout totals, and adapter upkeep stays under about 1 hour per week. **Cheapest test:** Phase 0 spike below. Make the go/no-go decision before any further build.

**Savings hypothesis (estimate, unverified).** The price spread on identical SKUs is about 5–15%, and delivery fees are about EGP 15–40 per order. Phase 0 measures the real numbers.

## Architecture

```
Phone (Telegram) ──long-poll──> Home PC: shopper service (Python 3.12)
                                 ├─ bot.py        Telegram I/O, restricted to allowed chat IDs
                                 ├─ web.py        FastAPI comparison table (reached over Tailscale, not public)
                                 ├─ matching.py   Claude API: parse list -> ItemSpec; verify matches
                                 ├─ optimizer.py  landed-cost basket split
                                 ├─ store.py      SQLite: offers, price history, sessions health
                                 └─ browser.py    one persistent Chromium profile (real Chrome channel)
                                      └─ adapters/<platform>.py  search / add_to_cart / fees / cart_url
```

- **Search method per adapter:** open the site in the persistent profile. Call the site's own JSON search endpoint from inside the page (`page.evaluate(fetch…)`), so cookies and the delivery address apply. If no JSON endpoint exists, fall back to DOM parsing. Throttle to human pace.
- **Two cart modes:**
  1. *Ready link*: Amazon multi-ASIN cart URL (`/gp/aws/cart/add.html?ASIN.1=…&Quantity.1=…`; format documented for PA-API, not yet verified on .eg), and the product URL for other platforms.
  2. *Push to cart*: the agent adds the items to your real cart on each chosen platform. You then open the app and check out. Assumption to verify: carts sync between web and app (talabat, noon, Amazon).
- **Never automated:** checkout and payment. You always confirm in the platform's own app.
- **Optimizer:** at most about 8 platforms, so enumerate the platform subsets (≤256). Assign each item to its cheapest platform in the subset, apply fees and minimum orders, and pick the lowest total. Constraints: maximum number of orders (default 2), exact-match-only toggle. The output shows **Best single platform**, **Best split** and **Savings vs. single**.
- **Freshness:** every price carries a timestamp. Re-check prices before push-to-cart if they are older than 30 minutes.
- **LLM:** Anthropic API direct (no gateway, as CLAUDE.md requires). Model `claude-sonnet-5-5` for parsing and matching, with Opus as an option. Load the `claude-api` skill when writing this code.

## Platform scope (New Cairo)

| Phase | Platforms | Basis |
|---|---|---|
| 1 | Amazon.eg, talabat (Mart + grocery stores), Carrefour Egypt, Gourmet Egypt | Web storefronts reachable; Gourmet and talabat pages show Add to Cart |
| 2 | noon (incl. Minutes), Seoudi, Spinneys, Instashop (covers Mahmoud ElFar) | Web present; bot-blocking from cloud means testing from the home PC |
| 4 (evaluate) | Breadfast, Rabbit, Oscar | App-only or unverified; Android-emulator route only if Phase 0–3 prove the value |

## Repo layout (new files)

- `pyproject.toml` — deps: playwright, fastapi, uvicorn, python-telegram-bot, anthropic, rapidfuzz, pydantic, pytest
- `src/shopper/config.py` — area, enabled platforms, maximum orders, allowed Telegram chat IDs (from `.env`, git-ignored)
- `src/shopper/models.py` — `ItemSpec`, `Offer`, `PlatformFees`, `Proposal`
- `src/shopper/browser.py` — persistent context at `~/.shopper/profile`, `login(platform)`, `health_check()`
- `src/shopper/adapters/base.py` — `Adapter` protocol: `search`, `fees`, `cart_url`, `add_to_cart`
- `src/shopper/adapters/{amazon_eg,talabat,carrefour,gourmet}.py` (Phase 1), more later
- `src/shopper/matching.py`, `optimizer.py`, `store.py`, `bot.py`, `web.py`, `cli.py`
- `tests/fixtures/<platform>/*.json|html` — saved responses for offline adapter tests
- `tests/test_optimizer.py`, `tests/test_matching.py`, `tests/test_adapters.py`
- `README.md` — setup, `shopper login <platform>`, Tailscale, Telegram token

## Phases

0. **Spike (2–3 days, go/no-go).** Persistent-profile login on Amazon.eg, talabat, Carrefour and Gourmet. Script a 10-item reference basket. Compare it with manual app prices. Gate: ≥95% of prices match, sessions survive a restart, and the measured savings justify the build.
1. **Single-item MVP.** 4 adapters, the `shopper search "<item>"` CLI, and the web table with links.
2. **Bulk + optimizer + push-to-cart.** List parsing, fee model, split proposal, cart push with confirmation.
3. **Telegram + ops.** Bot commands (`/add`, `/list`, `/compare`, `/push`, `/health`), session-expiry alerts, a price-history watchlist with drop alerts.
4. **Coverage.** Phase-2 platforms, then the app-only evaluation.

## Verification

- `pytest`: the optimizer on synthetic baskets (fees, minimum orders, max-orders cap), matching on a labelled set of about 50 item/candidate pairs, adapters against saved fixtures.
- Live smoke on the home PC: `shopper health`, then `shopper search "Juhayna full cream milk 1L"`. Expect an offer from every Phase-1 platform, with a URL and a timestamp.
- End-to-end: send the 10-item basket in Telegram. Check that the proposal arrives in under 2 minutes, the table link opens over Tailscale, and push-to-cart fills the carts. Cross-check the totals in each app before checkout.

## Measures

Price accuracy vs. the app (≥95%), match precision (≥90% exact on the labelled set), basket time (20 items in under 2 minutes), savings vs. best single platform (EGP and %), adapter break rate per week.

## Owners and next steps

1. **Claude**: scaffold the repo, models, optimizer and tests (TDD). Then build the Phase-1 adapters against fixtures. Commit to `claude/festive-mayer-zmadjk` and open a draft PR.
2. **Ahmed**: on the home PC, install Python 3.12 and run `shopper login <platform>` for each Phase-1 platform (OTP once each).
3. **Ahmed**: create a Telegram bot with @BotFather and put the token in `.env`. Install Tailscale on the PC and phone.
4. **Ahmed + Claude**: run the Phase-0 reference basket and decide go/no-go on the measured accuracy and savings.

Note: live adapter calibration needs the home PC. This cloud container gets bot-blocked, so here adapters are built from fixtures and selectors you capture with `shopper capture <platform>`.

## Findings during the build (2026-10-18)

- **Gourmet, Seoudi and Spinneys run Magento 2 with a public GraphQL catalogue.** Search returns name, SKU, stock and EGP price with no login, from any IP. They moved from "Phase 2" to live adapters. Gourmet keeps pack size in a `product_weight` attribute; the adapter reads it.
- **Amazon.eg, noon, talabat and Carrefour block this cloud IP, even with a real headless Chromium** (503 or HTTP/2 errors). Amazon is built from its documented search markup but needs a live check on the home PC. The others wait for a `shopper capture` from the home PC.
- **First live 5-item basket (no fees configured):** best split Seoudi + Spinneys EGP 1,199.00 vs best single (Seoudi) EGP 1,214.50. The split saves EGP 15.50 before fees. One delivery fee can erase it, which confirms premise 3: fees decide the plan.
- **Matching:** close matches of unknown size first won on price ("Almarai milk – Small" at EGP 15 beat 1L cartons). The ranking now puts accuracy first: fewest missing, then fewest close matches, then price.
