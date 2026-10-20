# Shops: what is covered, what is missing, how to add it

Surveyed on 2026-10-20 for New Cairo / 5th Settlement. Probes ran from a cloud server, at most three requests per site. A block from the cloud does not prove that the site blocks your home PC. "Unknown" means not verified.

## Covered now

| Shop | How shopper reads it | Status |
|---|---|---|
| Gourmet Egypt, Seoudi Market, Spinneys Egypt | Public Magento catalogue | Live |
| **Metro Markets (own site)** | Its search page (`/search?key=`) | **New, live.** Its Store Locator lists Fifth Settlement. Your delivery address and fee are not verified |
| **HyperOne** | Public Magento catalogue (`mcprod.hyperone.com.eg/graphql`) | **New, off by default.** No New Cairo branch found. Check the app, then set `enabled = true` in `config/platforms.toml` |
| talabat shops: talabat Mart, Carrefour, Spinneys, Metro, Lulu, Zaher Dairy, The Grove, Zidan | talabat's data routes, catalogue saved daily | Live |
| Amazon.eg | Shopping Chrome on the home PC | Live, with cart push |
| noon | Shopping Chrome | Live search; cart push presses the product page button |
| Instashop: Mahmoud ElFar (Mivida) | Shopping Chrome, results popup | Live search |
| **noon Minutes** (minutes.noon.com) | Shopping Chrome; search URL, then the page's search box | **New, built, not verified.** The cloud gets a bot check. A failed search saves the page for tuning |
| **Amazon Now** (amazon.eg tez browse, `qcbrand=sAuWWBROaG`) | Shopping Chrome; the page's search box, Amazon result cards | **New, built, not verified.** Same as noon Minutes |

## Missing, and the next step for each

| Shop | What is known | Route | Next step | Owner |
|---|---|---|---|---|
| Carrefour Egypt (own site) | Sends an empty page to scripts (bot protection) | Shopping Chrome, like noon | Low priority: Carrefour already comes through talabat. Add only if its own prices differ | Claude, on request |
| Kheir Zaman, Zahran, Mahmoud ElFar (Madinaty) | On talabat in some branches | talabat, already built | Run `shopper talabat-shops` on the home PC. If any of them deliver to you, paste its line into `shops = [...]` | You |
| More Instashop shops | Instashop lists many local supermarkets | Instashop, already built | Tell me the shop page names you use (the part after `/client/` in the link) | You |
| Breadfast | Website is marketing only; the shop API returned 404 | App only | No web route. Compare by hand, or skip | — |
| Rabbit | Website is marketing only; says it covers Greater Cairo and Giza | App only | No web route | — |
| Lulu (own site) | Cloudflare block from the cloud | Shopping Chrome | Low priority: Lulu already comes through talabat | — |
| Jumia, 2B, B.TECH (non-grocery) | Jumia and 2B show a Cloudflare challenge; B.TECH pages carry product data | Shopping Chrome (Jumia, 2B); page data (B.TECH) | Add if you want electronics compared. B.TECH is the quickest | Claude, on request |
| Oscar Grand Stores | Site certificate expired | None for now | Check whether Oscar is on talabat or Instashop | You |
| myLAHLOOB | Rehab only | App only | — | — |
| BIM, Fathalla, El Mahallawy, Ragab Sons | No online delivery found | None | — | — |
| Yaoota (price comparison site) | Its site fails at the connection stage | None | Possibly closed; not confirmed | — |
| Keeta, GoodsMart | Keeta is not in Egypt; GoodsMart has no news since 2021 | None | — | — |

Sources and full probe results: [retailers-research-2026-10-20.md](retailers-research-2026-10-20.md).
