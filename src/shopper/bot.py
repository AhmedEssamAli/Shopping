"""Telegram bot. Long polling, so the home PC needs no open port.

Send a message:
  one line           -> compare that item now
  several lines      -> compare that basket now
  /add <lines>       -> add to your saved list
  /list /remove N /clear
  /compare [exact]   -> compare the saved list
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .app import App

log = logging.getLogger(__name__)
HELP = __doc__.split("\n\n", 1)[1]
TG_LIMIT = 4000


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
            _, text, _ = await core.run(requests, exact_only=exact)
        except Exception as e:
            log.exception("compare failed")
            text = f"Comparison failed: {type(e).__name__}: {e}"
        await reply(update, text)

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

    bot.add_handler(CommandHandler(["start", "help"], start))
    bot.add_handler(CommandHandler("add", add))
    bot.add_handler(CommandHandler("list", show))
    bot.add_handler(CommandHandler("remove", remove))
    bot.add_handler(CommandHandler("clear", clear))
    bot.add_handler(CommandHandler("compare", compare_saved))
    bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return bot
