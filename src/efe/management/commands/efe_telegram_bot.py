"""Run the efeTrack Telegram bot (long polling; no public address needed)."""

import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from efe import telegram

logger = logging.getLogger(__name__)

IDLE_RETRY = 60  # no Telegram address configured: look again later
ERROR_RETRY = 10


class Command(BaseCommand):
    help = "Answer efeTrack commands (/yardim, /bugun, …) in the user's Telegram chat."

    def handle(self, *args, **options):
        offset = None
        registered = None
        while True:
            close_old_connections()
            try:
                owners = telegram.chat_owners()
            except Exception:
                logger.exception("Could not read Telegram addresses")
                time.sleep(ERROR_RETRY)
                continue
            if not owners:
                time.sleep(IDLE_RETRY)
                continue
            # One bot per server; with several tokens the first one is served.
            token, chats = next(iter(owners.items()))
            if registered != token:
                offset = None  # update numbers belong to one bot; start fresh for a new token
                telegram.api(
                    token,
                    "setMyCommands",
                    commands=[{"command": name, "description": text} for name, text in telegram.COMMANDS],
                )
                registered = token
                logger.info("Telegram bot ready for %s chat(s)", len(chats))

            updates = telegram.api(
                token,
                "getUpdates",
                http_timeout=60,
                offset=offset,
                timeout=50,  # Telegram long polling: wait up to 50 s for a message
                allowed_updates=["message", "callback_query", "inline_query"],
            )
            if updates is None:
                time.sleep(ERROR_RETRY)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                close_old_connections()
                try:
                    reply = telegram.process_update(update, chats)
                except Exception:
                    logger.exception("Telegram command failed")
                    callback = update.get("callback_query") or {}
                    message = update.get("message") or callback.get("message") or {}
                    chat = str((message.get("chat") or {}).get("id", ""))
                    reply = None
                    if chat in chats:
                        reply = {
                            "chat_id": chat,
                            "text": "Bir hata oldu, sonra tekrar dene.",
                            "keyboard": None,
                            "callback_id": callback.get("id"),
                        }
                if reply is None:
                    continue
                if reply.get("inline"):
                    telegram.api(
                        token, "answerInlineQuery", inline_query_id=reply["inline"], results=reply["results"],
                        cache_time=10, is_personal=True,
                    )
                    continue
                if reply.get("file") is not None:
                    telegram.api(token, "sendChatAction", chat_id=reply["chat_id"],
                                 action="upload_photo" if reply["file"].kind == "photo" else "upload_document")
                    if telegram.send_file(token, reply["chat_id"], reply["file"]) is None:
                        telegram.api(token, "sendMessage", chat_id=reply["chat_id"], text="Dosya gönderilemedi, sonra tekrar dene.")
                    continue
                if reply.get("callback_id"):
                    telegram.api(token, "answerCallbackQuery", callback_query_id=reply["callback_id"])
                if reply.get("edit"):
                    edited = telegram.api(token, "editMessageText", chat_id=reply["chat_id"], message_id=reply["edit"],
                                          text=reply["text"], reply_markup=reply.get("keyboard") or {"inline_keyboard": []})
                    if edited is not None:
                        continue
                if reply.get("clear_buttons"):
                    telegram.api(token, "editMessageReplyMarkup", chat_id=reply["chat_id"],
                                 message_id=reply["clear_buttons"], reply_markup={"inline_keyboard": []})
                payload = {"chat_id": reply["chat_id"]}
                if reply.get("keyboard"):
                    payload["reply_markup"] = reply["keyboard"]
                photo = reply.get("photo")
                if photo and len(reply["text"]) <= telegram.CAPTION_LIMIT:
                    if telegram.api(token, "sendPhoto", photo=photo, caption=reply["text"], **payload) is not None:
                        continue
                telegram.api(token, "sendMessage", text=reply["text"], **payload)
