"""Telegram bot. Long polling, so the home PC needs no open port.

Send a message:
  one line           -> compare that item now
  several lines      -> compare that basket now
  /add <lines>       -> add to the household list
  /list              -> the list, with buttons to remove lines, compare or clear
  /compare [exact]   -> compare the list
  /watch <item> [@ price]  -> alert me when it drops (or reaches the price)
  /watches           -> your watches, with buttons to stop them
  /push              -> add the last best plan to your carts (noon, Amazon)

The buttons under the keyboard do the common things. Under each result you get buttons
to add the plan to your carts, save it as the list, or open the full table. When several
products fit a line (a jar, a pouch, a sachet), the bot asks which one you mean.
"""
from __future__ import annotations

import asyncio
import logging
import zlib

from telegram import (BotCommand, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update)
from telegram.constants import ChatAction
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .app import App
from .report import label
from .service import CompareResult
from .watcher import best_exact, check_watches, parse_watch

log = logging.getLogger(__name__)
HELP = __doc__.split("\n\n", 1)[1]
TG_LIMIT = 4000  # Telegram allows 4096 characters per message
BUTTON_TEXT = 60  # Telegram cuts long button labels
LIST_BUTTONS = 20  # remove buttons shown under /list

# The keyboard that stays under the text box. Its buttons arrive as plain text.
COMPARE_LIST, MY_LIST, WATCHES, HOW = "Compare my list", "My list", "My watches", "How to use"
MENU = ReplyKeyboardMarkup([[COMPARE_LIST, MY_LIST], [WATCHES, HOW]], resize_keyboard=True, is_persistent=True,
                           input_field_placeholder="Type an item or paste a list")
# When /add or /watch come without text, the bot asks with these, and reads your reply.
ADD_PROMPT = "Send the lines to add, one item per line."
WATCH_PROMPT = "Send the item to watch, with a target price if you want one: Nescafe classic 200g @ 250"
COMMANDS = [
    BotCommand("list", "Show the household list"),
    BotCommand("compare", "Compare the household list"),
    BotCommand("add", "Add lines to the list"),
    BotCommand("push", "Add the last best plan to your carts"),
    BotCommand("watch", "Watch an item's price"),
    BotCommand("watches", "Show your watches"),
    BotCommand("help", "How to use the bot"),
]


def pick_keyboard(core: App, result: CompareResult, rid: str, i: int) -> InlineKeyboardMarkup:
    """One button per product card: "200g, jar — 300.00 Gourmet, Seoudi"."""
    rows = []
    for j, c in enumerate(result.items[i].cards):
        shops = ", ".join(label(core.settings, p) for p in c.per_platform())
        other = "" if c.exact else " (other size)"
        text = f"{c.label}{other} — {c.cheapest().price:,.2f} {shops}"
        rows.append([InlineKeyboardButton(text[:BUTTON_TEXT], callback_data=f"pk:{rid}:{i}:{c.key}")])
    return InlineKeyboardMarkup(rows)


def result_keyboard(core: App, result: CompareResult, rid: str, pushed: bool = False) -> InlineKeyboardMarkup:
    """Under a result: cart push (once, and only when the plan is final and a shop in it
    supports it), save as list, full table."""
    rows = []
    if not pushed and core.pushable(result) and not any(r.needs_pick for r in result.items):
        rows.append([InlineKeyboardButton("Add the plan to my carts", callback_data=f"push:{rid}")])
    rows.append([InlineKeyboardButton("Save as my list", callback_data=f"save:{rid}")])
    url = core.result_url(rid)
    if url.startswith("https://"):  # Telegram refuses links to 127.0.0.1
        rows.append([InlineKeyboardButton("Open the full table", url=url)])
    return InlineKeyboardMarkup(rows)


def line_tag(query: str) -> str:
    """Short fingerprint of a list line, so a stale Remove button cannot hit another line."""
    return f"{zlib.crc32(query.encode()) & 0xFFFF:04x}"


def list_keyboard(items) -> InlineKeyboardMarkup:
    n = len(items)
    rows = [[InlineKeyboardButton(f"Remove {i + 1}", callback_data=f"rm:{i}:{line_tag(items[i].query)}")
             for i in range(k, min(k + 4, n, LIST_BUTTONS))] for k in range(0, min(n, LIST_BUTTONS), 4)]
    if n:
        rows.append([InlineKeyboardButton("Compare the list", callback_data="cmp"),
                     InlineKeyboardButton("Clear the list", callback_data="clr")])
    return InlineKeyboardMarkup(rows)


def parse_callback(data: str) -> tuple[str, str, int, str | None] | None:
    """"pk:<rid>:<item>:<card key>" (pick) or "fg:<rid>:<item>" (choose again)."""
    parts = (data or "").split(":")
    try:
        if parts[0] == "pk" and len(parts) == 4:
            return "pick", parts[1], int(parts[2]), parts[3]
        if parts[0] == "fg" and len(parts) == 3:
            return "forget", parts[1], int(parts[2]), None
    except ValueError:
        pass
    return None


def chunks(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Split at line breaks so a message never cuts a line (or a link) in half."""
    out, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + 1 + len(line) > limit:
            out.append(cur)
            cur = ""
        while len(line) > limit:  # one very long line: hard cut, after what came before it
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return [p for p in out if p.strip()]


def alert_keyboard(alert) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("Stop watching", callback_data=f"uw:{alert.watch_id}")]]
    if alert.url.startswith("https://"):
        rows.insert(0, [InlineKeyboardButton("Open the product", url=alert.url)])
    return InlineKeyboardMarkup(rows)


async def setup_menu(bot: Application) -> None:
    """The "/" menu in Telegram. Best effort: the bot works without it."""
    try:
        await bot.bot.set_my_commands(COMMANDS)
    except Exception:
        log.warning("Could not set the Telegram command menu", exc_info=True)


def build_bot(core: App) -> Application:
    allowed = core.settings.allowed_chat_ids
    bot = Application.builder().token(core.settings.telegram_token).build()

    def ok(update: Update) -> bool:
        return update.effective_chat.id in allowed

    async def reply(update: Update, text: str, markup=None, html: bool = False) -> None:
        parts = chunks(text)
        for k, part in enumerate(parts):
            await update.effective_message.reply_text(
                part, disable_web_page_preview=True, reply_markup=markup if k == len(parts) - 1 else None,
                parse_mode="HTML" if html else None)

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not ok(update):
            await reply(update, f"Your chat ID is {chat_id}. Add it to TELEGRAM_ALLOWED_CHAT_IDS in .env and restart.")
            return
        await reply(update, HELP, MENU)

    async def run_compare(update: Update, requests, exact: bool = False) -> None:
        if not requests:
            await reply(update, "The list is empty. Type an item, or add lines with /add.")
            return
        await update.effective_chat.send_action(ChatAction.TYPING)
        n = len(requests)
        waiting = await update.effective_message.reply_text(
            f"Searching {n} item{'s' if n != 1 else ''} in every shop. This takes up to a minute.")
        try:
            result, _, rid = await core.run(requests, exact_only=exact)
        except Exception as e:
            log.exception("compare failed")
            await reply(update, f"The comparison failed ({type(e).__name__}: {e}). Try again; if it repeats, "
                                "check that shopper serve and Shopping Chrome are running.")
            return
        finally:
            try:
                await waiting.delete()
            except Exception:
                pass
        core.last_results[update.effective_chat.id] = result
        await reply(update, core.chat_text(rid), result_keyboard(core, result, rid), html=True)
        for i, r in enumerate(result.items):
            if r.needs_pick:
                await update.effective_message.reply_text(
                    f"Which one do you mean? {r.request.qty} × {r.request.query}",
                    reply_markup=pick_keyboard(core, result, rid, i))

    async def show_list(update: Update, edit: bool = False) -> None:
        items = core.list_items()
        text = ("\n".join(f"{i + 1}. {it.qty} × {it.query}" for i, it in enumerate(items))
                or "The list is empty. Add lines with /add, or press Save as my list under a result.")
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text[:TG_LIMIT], reply_markup=list_keyboard(items))
        else:
            await reply(update, text, list_keyboard(items))

    async def show_watches(update: Update, edit: bool = False) -> None:
        rows = core.store.list_watches(update.effective_chat.id)
        lines = [f"#{w.id} {w.query}" + (f" @ {w.target:,.2f}" if w.target is not None else "")
                 + (f" — now {w.last_price:,.2f} ({label(core.settings, w.last_platform)})" if w.last_price is not None else "")
                 for w in rows]
        text = "\n".join(lines) or "No watches. Add one: /watch Nescafe classic 200g @ 250"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(f"Stop #{w.id}", callback_data=f"uw:{w.id}")] for w in rows[:LIST_BUTTONS]])
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text[:TG_LIMIT], reply_markup=markup)
        else:
            await reply(update, text, markup)

    async def push_result(update: Update, result: CompareResult | None) -> None:
        if not result:
            await reply(update, "Compare something first, then add it to your carts.")
            return
        await update.effective_chat.send_action(ChatAction.TYPING)
        await reply(update, "\n".join(await core.push_carts(result)))

    async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        text = update.effective_message.text.strip()
        asked = getattr(getattr(update.effective_message, "reply_to_message", None), "text", "") or ""
        if asked == ADD_PROMPT:
            await add_lines(update, text)
        elif asked == WATCH_PROMPT:
            await add_watch(update, text)
        elif text == COMPARE_LIST:
            await run_compare(update, core.list_items())
        elif text == MY_LIST:
            await show_list(update)
        elif text == WATCHES:
            await show_watches(update)
        elif text == HOW:
            await reply(update, HELP, MENU)
        else:
            await run_compare(update, await core.parse_async(text))

    async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not ok(update):
            await query.answer()
            return
        data = query.data or ""
        kind, _, rest = data.partition(":")
        if kind in ("pk", "fg"):
            await on_pick(update, query)
        elif kind in ("push", "save"):
            result = core.results.get(rest)
            if result is None:
                await query.answer("This comparison is no longer open. Send the list again.", show_alert=True)
            elif kind == "push":
                await query.answer("Adding to your carts")
                try:  # one push per result: a second tap would add everything twice
                    await query.edit_message_reply_markup(result_keyboard(core, result, rest, pushed=True))
                except Exception:
                    pass
                await push_result(update, result)
            else:
                core.save_list([r.request for r in result.items])
                await query.answer("Saved as your list")
                await reply(update, f"Saved {len(result.items)} line(s) as the household list.")
        elif kind == "rm":
            index, _, tag = rest.partition(":")
            items = core.list_items()
            i = int(index) if index.isdigit() else -1
            removed = 0 <= i < len(items) and line_tag(items[i].query) == tag and core.remove_from_list(i, items[i].query)
            await query.answer("Removed" if removed else "The list changed; nothing was removed")
            await show_list(update, edit=True)
        elif kind == "cmp":
            await query.answer()
            await run_compare(update, core.list_items())
        elif kind == "clr":
            core.clear_list()
            await query.answer("List cleared")
            await show_list(update, edit=True)
        elif kind == "uw" and rest.isdigit():
            done = core.store.delete_watch(update.effective_chat.id, int(rest))
            await query.answer("Stopped" if done else "That watch is already gone")
            await show_watches(update, edit=True)
        else:
            await query.answer()

    async def on_pick(update: Update, query) -> None:
        parsed = parse_callback(query.data)
        result = core.results.get(parsed[1]) if parsed else None
        if not parsed or result is None:
            await query.answer("This comparison is no longer open. Send the list again.", show_alert=True)
            return
        action, rid, i, key = parsed
        r = result.items[i] if 0 <= i < len(result.items) else None
        chosen = next((c.label for c in r.cards if c.key == key), None) if r else None
        if core.feedback(rid, action, i, card=key) != "ok":
            await query.answer("Already done, or that choice is no longer available.", show_alert=True)
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
            await reply(update, "<b>Updated plan</b>\n" + core.chat_text(rid), result_keyboard(core, result, rid), html=True)

    async def add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        text = update.effective_message.text.partition(" ")[2].strip()
        if not text:
            await update.effective_message.reply_text(ADD_PROMPT, reply_markup=ForceReply(input_field_placeholder="2 milk 1L"))
            return
        await add_lines(update, text)

    async def add_lines(update: Update, text: str) -> None:
        items = await core.parse_async(text)
        if not items:
            await reply(update, "Nothing to add. Send one item per line, like 2 Juhayna full cream milk 1L.")
            return
        core.add_to_list(items)
        await reply(update, f"Added {len(items)}. The list has {len(core.list_items())} line(s).", list_keyboard(core.list_items()))

    async def show(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if ok(update):
            await show_list(update)

    async def remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        try:
            n = int(ctx.args[0])
        except (IndexError, ValueError):
            await reply(update, "Usage: /remove 3, or press a Remove button under /list.")
            return
        await reply(update, "Removed." if core.remove_from_list(n - 1) else "No such line.")

    async def clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if ok(update):
            core.clear_list()
            await reply(update, "List cleared.")

    async def compare_saved(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        exact = bool(ctx.args) and ctx.args[0].lower() == "exact"
        await run_compare(update, core.list_items(), exact)

    async def watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        text = update.effective_message.text.partition(" ")[2].strip()
        if not text:
            await update.effective_message.reply_text(WATCH_PROMPT, reply_markup=ForceReply(input_field_placeholder="Nescafe classic 200g @ 250"))
            return
        await add_watch(update, text)

    async def add_watch(update: Update, text: str) -> None:
        query, target = parse_watch(text)
        if not query:
            await reply(update, WATCH_PROMPT)
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
        if ok(update):
            await show_watches(update)

    async def unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ok(update):
            return
        try:
            wid = int(ctx.args[0].lstrip("#"))
        except (IndexError, ValueError):
            await reply(update, "Usage: /unwatch 3, or press a Stop button under /watches.")
            return
        await reply(update, "Stopped." if core.store.delete_watch(update.effective_chat.id, wid) else "No such watch.")

    async def push(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if ok(update):
            await push_result(update, core.last_results.get(update.effective_chat.id))

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
            for alert in await check_watches(core):
                await bot.bot.send_message(alert.chat_id, alert.text, disable_web_page_preview=True,
                                           reply_markup=alert_keyboard(alert))
        except Exception:
            log.exception("watch check failed")
        await asyncio.sleep(interval_s)
