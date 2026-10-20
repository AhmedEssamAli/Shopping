"""Telegram bot. Long polling, so the home PC needs no open port.

Send a message:
  one line           -> compare that item now
  several lines      -> compare that basket now
  /add <lines>       -> add to your saved list
  /list /remove N /clear
  /compare [exact]   -> compare the saved list
  /watch <item> [@ price]  -> alert me when it drops (or reaches the price)
  /watches /unwatch N
  /push              -> add your last best plan to your carts where supported (noon, Amazon)

When several products fit a line (a jar, a pouch, a sachet), the bot asks you to choose
with buttons. Your choice is remembered for that line, and the plan is sent again.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .app import App
from .report import label
from .service import CompareResult
from .watcher import best_exact, check_watches, parse_watch

log = logging.getLogger(__name__)
HELP = __doc__.split("\n\n", 1)[1]
TG_LIMIT = 4000
BUTTON_TEXT = 60  # Telegram cuts long button labels


def pick_keyboard(core: App, result: CompareResult, rid: str, i: int) -> InlineKeyboardMarkup:
    """One button per product card: "200g · jar — 300.00 Gourmet, Seoudi"."""
    rows = []
    for j, c in enumerate(result.items[i].cards):
        shops = ", ".join(label(core.settings, p) for p in c.per_platform())
        text = f"{c.label} — {c.cheapest().price:,.2f} {shops}"
        rows.append([InlineKeyboardButton(text[:BUTTON_TEXT], callback_data=f"pk:{rid}:{i}:{j}")])
    return InlineKeyboardMarkup(rows)


def parse_callback(data: str) -> tuple[str, str, int, int | None] | None:
    """"pk:<rid>:<item>:<card>" (pick) or "fg:<rid>:<item>" (choose again)."""
    parts = (data or "").split(":")
    try:
        if parts[0] == "pk" and len(parts) == 4:
            return "pick", parts[1], int(parts[2]), int(parts[3])
        if parts[0] == "fg" and len(parts) == 3:
            return "forget", parts[1], int(parts[2]), None
    except ValueError:
        pass
    return None


def build_bot(core: App) -> Application:
    allowed = core.settings.allowed_chat_ids
    bot = Application.builder().token(core.settings.telegram_token).build()

    def ok(update: Update) -> bool:
        return update.effective_chat.id in allowed

    async def reply(update: Update, text: str) -> None:
        for i in range(0, len(text), TG_LIMIT):
            await update.effective_message.reply_text(text[i:i + TG_LIMIT], disable_web_page_preview=True)

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not ok(update):
            await reply(update, f"Your chat ID is {chat_id}. Add it to TELEGRAM_ALLOWED_CHAT_IDS in .env and restart.")
            return
        await reply(update, HELP)

    async def run_compare(update: Update, requests, exact: bool = False) -> None:
        if not requests:
            await reply(update, "List is empty.")
            return
        await update.effective_chat.send_action(ChatAction.TYPING)
        await reply(update, f"Searching {len(requests)} item(s)…")
        try:
            result, text, rid = await core.run(requests, exact_only=exact)
            core.last_results[update.effective_chat.id] = result
            text += "\nSend /push to add this plan to your carts."
        except Exception as e:
            log.exception("compare failed")
            await reply(update, f"Comparison failed: {type(e).__name__}: {e}")
            return
        await reply(update, text)
        await ask_picks(update, result, rid)

    async def ask_picks(update: Update, result: CompareResult, rid: str) -> None:
        for i, r in enumerate(result.items):
            if r.needs_pick:
                await update.effective_message.reply_text(
                    f"Which one do you mean? {r.request.qty} × {r.request.query}",
                    reply_markup=pick_keyboard(core, result, rid, i))

    async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not ok(update):
            await query.answer()
            return
        parsed = parse_callback(query.data)
        result = core.results.get(parsed[1]) if parsed else None
        if not parsed or result is None:
            await query.answer("This comparison is no longer open. Send the list again.", show_alert=True)
            return
        action, rid, i, j = parsed
        r = result.items[i] if 0 <= i < len(result.items) else None
        chosen = r.cards[j].label if r and action == "pick" and j is not None and 0 <= j < len(r.cards) else None
        if not core.feedback(rid, action, i, card=j):
            await query.answer("That choice is no longer available. Send the list again.", show_alert=True)
            return
        await query.answer("Saved")
        if action == "pick":
            again = InlineKeyboardMarkup([[InlineKeyboardButton("Choose again", callback_data=f"fg:{rid}:{i}")]])
            await query.edit_message_text(f"Chosen for {r.request.query}: {chosen}. I will use it next time too.",
                                          reply_markup=again)
        else:
            await query.edit_message_text(f"Which one do you mean? {r.request.qty} × {r.request.query}",
                                          reply_markup=pick_keyboard(core, result, rid, i))
        if not any(x.needs_pick for x in result.items):
            await reply(update, "Updated plan:\n" + core.text(rid))

    async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        await run_compare(update, core.parse(update.effective_message.text))

    async def add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        text = update.effective_message.text.partition(" ")[2] or ""
        items = core.parse(text)
        core.store.add_to_list(update.effective_chat.id, items)
        await reply(update, f"Added {len(items)}. List has {len(core.store.get_list(update.effective_chat.id))} item(s).")

    async def show(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        items = core.store.get_list(update.effective_chat.id)
        await reply(update, "\n".join(f"{i + 1}. {it.qty} × {it.query}" for i, it in enumerate(items)) or "List is empty.")

    async def remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        try:
            n = int(ctx.args[0])
        except (IndexError, ValueError):
            await reply(update, "Usage: /remove 3")
            return
        done = core.store.remove_from_list(update.effective_chat.id, n - 1)
        await reply(update, "Removed." if done else "No such line.")

    async def clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        core.store.clear_list(update.effective_chat.id)
        await reply(update, "List cleared.")

    async def compare_saved(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        exact = bool(ctx.args) and ctx.args[0].lower() == "exact"
        await run_compare(update, core.store.get_list(update.effective_chat.id), exact)

    async def watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        query, target = parse_watch(update.effective_message.text.partition(" ")[2])
        if not query:
            await reply(update, "Usage: /watch Nescafe classic 200g @ 250")
            return
        wid = core.store.add_watch(update.effective_chat.id, query, target)
        m = await best_exact(core, query)
        if m:
            core.store.update_watch(wid, m.offer.price, m.offer.platform, m.offer.url)
            now = f"Best now: EGP {m.offer.price:,.2f} at {label(core.settings, m.offer.platform)} — {m.offer.name}"
        else:
            now = "No exact match right now; I will keep checking."
        goal = f" Target EGP {target:,.2f}." if target is not None else ""
        await reply(update, f"Watching #{wid}: {query}.{goal}\n{now}")

    async def watches(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        rows = core.store.list_watches(update.effective_chat.id)
        lines = [f"#{w.id} {w.query}" + (f" @ {w.target:,.2f}" if w.target is not None else "")
                 + (f" — now {w.last_price:,.2f} ({label(core.settings, w.last_platform)})" if w.last_price is not None else "") for w in rows]
        await reply(update, "\n".join(lines) or "No watches. Add one: /watch Nescafe classic 200g @ 250")

    async def unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        try:
            wid = int(ctx.args[0].lstrip("#"))
        except (IndexError, ValueError):
            await reply(update, "Usage: /unwatch 3")
            return
        await reply(update, "Removed." if core.store.delete_watch(update.effective_chat.id, wid) else "No such watch.")

    async def push(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        result = core.last_results.get(update.effective_chat.id)
        if not result:
            await reply(update, "Compare something first, then /push.")
            return
        await reply(update, "\n".join(await core.push_carts(result)))

    bot.add_handler(CommandHandler(["start", "help"], start))
    bot.add_handler(CommandHandler("push", push))
    bot.add_handler(CommandHandler("watch", watch))
    bot.add_handler(CommandHandler("watches", watches))
    bot.add_handler(CommandHandler("unwatch", unwatch))
    bot.add_handler(CommandHandler("add", add))
    bot.add_handler(CommandHandler("list", show))
    bot.add_handler(CommandHandler("remove", remove))
    bot.add_handler(CommandHandler("clear", clear))
    bot.add_handler(CommandHandler("compare", compare_saved))
    bot.add_handler(CallbackQueryHandler(on_button))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return bot


async def watch_loop(core: App, bot: Application, interval_s: float, first_delay_s: float = 60) -> None:
    """Re-price every watch on a timer and message the owner when one fires."""
    await asyncio.sleep(first_delay_s)
    while True:
        try:
            for chat_id, text in await check_watches(core):
                await bot.bot.send_message(chat_id, text, disable_web_page_preview=True)
        except Exception:
            log.exception("watch check failed")
        await asyncio.sleep(interval_s)
