# Spec: easy bulk ordering and a new page design

Owner: Ahmed (product), Claude (build) · Status: built 2026-10-21 (Phases 1–4; colour: green) · Date: 2026-10-20

## Problem statement

A weekly basket has 15 to 30 lines, and the cheapest plan often splits it into 2 or 3 shop orders. Today shopper is good at the comparison but weak at the steps around it:

- **Entering the list is slow.** The home page shows two item rows; a 20-line basket needs 18 clicks on "Add another item", or a pasted list that goes straight to the comparison without a chance to check how each line was read.
- **Quantities are hard to change.** Quantity is typed once. The household list and the result page cannot change it, so a wrong quantity means a new comparison and a new search of every shop (up to a minute).
- **A split plan does not guide the orders.** The result page shows which shop gets which line, but not a clear "order 1 of 3" path: what to add to each cart, which carts shopper can fill, and what is already done.
- **The page looks dated and dense.** Buttons are small, sections run together, and the main action is not obvious on a phone.

Evidence: your requests on 2026-10-18 to 2026-10-20 ("different things than what I wrote", "item, brand, size, pieces", this request) and the current code (facts, checked in `web.py` and `report.py`). Cost of not solving: you open the apps by hand for split orders, which removes most of the time saving.

## Goals

1. **Enter a 20-line basket in under 2 minutes** from a paste or from the saved list, with every line checked before the search.
2. **Change any quantity in one tap**, anywhere (form, list, result), and see the new plan in under 1 second without searching again.
3. **Place a 3-shop plan in under 3 minutes** from the result page: each order has one clear action, and progress shows "2 of 3 orders placed".
4. **One obvious next step on every screen** on a 390 px phone: one primary button per section, 48 px tall.

## Non-goals

- **Checkout and payment.** shopper never pays. You confirm each order in the shop's app. (Safety, and the shops' terms.)
- **Cart push for more shops in this release.** Only noon and Amazon.eg fill carts today. noon Minutes and Amazon Now push is a separate piece of work (see Future). Other shops get a copy-ready list instead.
- **Changes to matching or the learner.** This spec is about entry, quantities, orders and design, not about which product is chosen.
- **Accounts or several households.** shopper stays one household on one PC.
- **A native app.** The installable web app (PWA) and Telegram stay the two interfaces.

## Users

One persona: **the household shopper** (you, and anyone in `TELEGRAM_ALLOWED_CHAT_IDS`), on a phone over Tailscale, sometimes on the PC. They know what they want, hate retyping, and order 1 to 3 times a week.

## User stories (priority order)

1. As the household shopper, I want to paste my whole list and see each line split into item, brand, type, size, pieces and quantity, so that I can fix a misread line before shopper searches.
2. As the household shopper, I want `−` and `+` buttons next to every quantity, so that I change "2 milk" to "3 milk" with one tap.
3. As the household shopper, I want the plan to update at once when I change a quantity or remove a line on the result page, so that I do not wait for a new search.
4. As the household shopper, I want the result page to show my plan as numbered orders, one card per shop, so that I know exactly what to do next.
5. As the household shopper, I want each order card to give me its one action (add to cart, or copy the list and open the shop) and a "Done" tick, so that I can place 3 orders without losing track.
6. As the household shopper, I want to choose "1 shop / up to 2 / up to 3" on the result page, so that I trade savings against effort without starting again.
7. As the household shopper, I want to reorder my last basket or a saved list in one tap, so that the weekly shop starts from what I bought before.
8. Edge cases:
   - A line that no shop has: the review table marks it "Not found" and keeps it out of the orders, without blocking the rest.
   - Quantity 0 or empty: the line leaves the plan (an undo shows for 5 seconds).
   - A shop that falls under its minimum order after a quantity change: the plan moves lines or explains why it cannot.
   - A result from before a restart: the page says the quantities cannot change and offers "Compare again".

## Requirements

### P0: must have

**R1. Review step for bulk entry.** Pasting a list, or pressing "Compare the list", first shows a review table: one row per line, with the six fields from `entry.py` already filled, plus quantity steppers and a remove button. The primary button is "Compare N items".
- [ ] Given a pasted list of 20 lines, when you press "Check my list", then 20 editable rows appear, read as `entry.read_entry` reads them, in under 1 second (local parser) or under 10 seconds (Claude).
- [ ] Editing a field updates that row's "Searches for" line at once.
- [ ] "Compare N items" searches exactly the rows shown, with their quantities.
- [ ] An empty or unreadable line is shown as a row to fix, not dropped without notice.

**R2. Quantity steppers everywhere.** Every quantity (item rows, review table, household list, result lines) has `−`, the number, and `+`. The tap targets are at least 44 × 44 px. Typing a number still works.
- [ ] One tap on `+` or `−` changes the quantity by 1; the number never goes below 0.
- [ ] On the household list, the change is saved at once (no Save button).
- [ ] Telegram `/list` gets the same: a `−`/`+` row per line (callback data stays under 64 bytes).

**R3. Recompute without searching.** On the result page, changing a quantity, removing a line or changing the order limit re-runs the plan (`service.assemble`) from the offers already fetched.
- [ ] The new totals, fees, savings line and order cards show in under 1 second, with no shop search.
- [ ] Fee rules apply to the new subtotals (free-delivery limits, small-order fees, service-fee floor and cap).
- [ ] The page says "Prices fetched HH:MM" and offers "Refresh prices" when prices are older than 30 minutes.
- [ ] A result from history (after a restart) shows the steppers disabled and a "Compare again" button.

**R4. Orders view.** The best plan is shown as numbered order cards ("Order 1 of 3 · Seoudi Market"), each with: shop, item count, subtotal, fees, total, the lines with photo and quantity, and one primary action.
- [ ] Shops with cart push (noon, Amazon.eg): the action is "Add N items to cart". It runs once and then shows what was added.
- [ ] Other shops: the action is "Copy list and open shop". It copies the lines as text (`2 × Juhayna Skimmed Milk 1L`) and opens the shop's site or app link.
- [ ] Each card has a "Done" tick. The page header shows "2 of 3 orders placed". Ticks are kept for the result while shopper runs.
- [ ] The card order is largest order first.

**R5. Order limit on the result page.** A segmented control "1 shop · up to 2 · up to 3" sits above the orders and shows each option's total, for example "1 shop EGP 812 · up to 2 EGP 774".
- [ ] Choosing an option applies R3 (recompute only).

**R6. New page design.** The layout, buttons and type follow pdf2go.com's system, measured from its CSS on 2026-10-20; the colours are new (see Design system below).
- [ ] Every page uses the type scale, button styles, card styles and 1152 px container below.
- [ ] Each section has one primary button. All other actions are secondary or text buttons.
- [ ] At 390 px there is no sideways scroll, and the primary action of the visible step is on screen (sticky action bar on long pages).
- [ ] Dark mode keeps contrast at WCAG AA (4.5:1 for body text).

### P1: nice to have

- **P1-1 Saved lists.** Name and keep several lists ("Weekly", "Monthly", "Party"). Each has "Review and compare" and "Reorder last basket". It extends the household list, which becomes the default list.
- **P1-2 Quick add from history.** In the review table and the item rows, typing shows products you chose before (`memory.chosen_lines`) as suggestions with their last price.
- **P1-3 Telegram orders view.** After a comparison, the bot sends one message per order with "Add to cart" or "Copy list" and "Done" buttons.
- **P1-4 Event log for the measures.** A small SQLite table records the timestamps for entry start, compare, each order done, and each quantity change, so the success metrics below can be read without a stopwatch.

### P2: future, but plan for it now

- **Cart push for noon Minutes and Amazon Now** through their "+" buttons. Keep R4's action slot generic (`push`, `copy`, `link`), so a shop can move from "copy" to "push" with no page change.
- **Talabat and Instashop deep links to the item**, if the shops expose them.
- **Shared ticks between phones** (a second household member sees "Order 2 done"). Keep ticks keyed by result id and shop, not stored in the browser.

## Design system (R6)

Taken from pdf2go.com (fact: its CSS, read 2026-10-20), except colours.

| Element | pdf2go value | shopper use |
|---|---|---|
| UI font | Inter, system-ui | all body text, buttons, fields |
| Heading font | Poppins (Inter fallback) | page and section titles |
| Page title | `clamp(1.875rem, 3.6vw, 2.75rem)`, bold, line-height 1.1, letter-spacing −0.03em, max 22ch | "What do you need?", "Your plan" |
| Section title | 24 px (30 px from 640 px), bold, −0.02em, max 30ch | "Your orders", "The household list" |
| Body | 14 px for secondary text, 16 px for main text | same |
| Text case | sentence case for headings, buttons and labels | same |
| Primary button | min height 48 px, radius 12 px, padding 12 × 28 px, 16 px semibold, shadow `0 8px 20px -6px rgba(23,23,23,.35)`, hover lifts 2 px with a larger shadow, 200 ms | one per section |
| Secondary button | same size, transparent, 2 px border in the primary colour, fills on hover | "Add to the list", "Copy list" |
| Cards | radius 16 px (featured 24 px), `shadow-sm`, padding 20 px | order cards, list, shelf labels |
| Featured card | radius 24 px, filled primary, white text, icon in a 56 px rounded square | the best plan |
| Chips | radius 12 px, 14 px text | "Products you chose before", the read fields |
| Container | max 72 rem (1152 px), 16 px gutters on phones | all pages |
| Grid | 12 columns from 768 px; 5 + 7 split for "featured + list" | plan card next to the order cards |
| Steps | numbered steps with bold titles (their tool page: 1 to 4) | header steps "1 Add items · 2 Review · 3 Compare · 4 Order" |

**New colours (recommendation).** It is a grocery tool, so keep green as the brand, but cleaner and lighter than today's basil and yellow tag:

| Token | Light | Dark | Use |
|---|---|---|---|
| `--primary` | `#16A34A` | `#22C55E` | primary buttons, featured plan card |
| `--primary-hover` | `#15803D` | `#16A34A` | hover |
| `--accent` | `#F59E0B` | `#FBBF24` | savings, deals, "best" badge |
| `--danger` | `#DC2626` | `#F87171` | "not this", errors |
| `--ink` | `#0F172A` | `#E2E8F0` | text |
| `--muted` | `#64748B` | `#94A3B8` | secondary text |
| `--page` | `#F8FAFC` | `#0B1220` | page background |
| `--card` | `#FFFFFF` | `#111827` | cards |
| `--line` | `#E2E8F0` | `#1F2937` | borders |

Assumption: you want to keep a green identity. If you prefer pdf2go's own direction (a strong single colour), swap `--primary` for indigo `#4F46E5`; nothing else changes.

## Success metrics

Measured on your own use. Until P1-4 exists, measure with a stopwatch on two real baskets.

| Type | Metric | Today (estimate, not measured) | Target | Stretch |
|---|---|---|---|---|
| Leading | Time to enter and check a 20-line basket | 4–6 min | < 2 min | < 1 min from a saved list |
| Leading | Taps to change one quantity | 3–5 plus a new search | 1, no search | 1 |
| Leading | Time to new plan after a quantity change | 30–60 s (new search) | < 1 s | < 0.3 s |
| Leading | Time to place a 3-shop plan from the result page | 8–10 min | < 3 min | < 2 min |
| Lagging | Comparisons per week that end with all orders ticked "Done" | 0 (no ticks yet) | ≥ 80% after 4 weeks | 100% |
| Lagging | New searches caused only by a quantity fix | not tracked | 0 | 0 |

Review after 2 weeks of use, then after 6 weeks.

## Open questions

| Question | Who answers | Blocking? |
|---|---|---|
| Keep green, or move to another main colour? | Ahmed (design) | Yes, for R6 colours only; layout work can start |
| Which shops should "Copy list and open shop" open: the website or the phone app link? | Ahmed | No |
| Is a "Done" tick enough, or do you want shopper to check each cart total against the plan (noon and Amazon only)? | Ahmed | No |
| Should quantity steppers in Telegram be on every line, or behind one "Change quantities" button to keep messages short? | Ahmed (after trying R2) | No |
| Can Seoudi, Gourmet and Spinneys links add items to their carts from a URL? Not verified. | Claude (engineering) | No (P2) |

## Timeline and phasing

No hard deadline. Suggested order, each one shippable on its own:

1. **Phase 1, design base (R6):** tokens, fonts, buttons, cards, container, step header. All pages at once, so every later piece uses it. About 1 session.
2. **Phase 2, quantities (R2, R3, R5):** steppers, recompute on the result page, order limit control. About 1 session.
3. **Phase 3, bulk entry and orders (R1, R4):** review table, order cards with actions and ticks. About 1–2 sessions.
4. **Phase 4, P1 items**, in this order: saved lists, Telegram orders view, quick add, event log.

Dependency: R3 needs the fetched offers kept with each result in memory (they are today: `CompareResult` holds them until restart). Results from before a restart stay read-only (R3 edge case).

## Next steps

1. **Ahmed:** read this spec, answer the colour question, and cut or add anything in P0.
2. **Claude:** after your answer, build Phase 1, with screenshots at 390 px and 1200 px for your review.
3. **Claude:** build Phases 2 and 3, each with tests and a short demo on your saved list.
4. **Ahmed:** time two real baskets against the targets above after Phase 3.
