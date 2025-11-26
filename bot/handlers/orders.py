# bot/handlers/orders.py
import logging
from datetime import datetime, timezone

from aiogram import Dispatcher, F
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message

from ..ai.classifier import classify_text_ai
from ..config import Settings
from ..storage import (
    get_or_create_session,
    get_session_key,
    is_session_ready,
    finalize_session,
    clear_session,
    save_order_to_json,
)
from ..utils.locations import extract_location_from_message
from ..utils.phones import extract_phones

logger = logging.getLogger(__name__)


def register_order_handlers(dp: Dispatcher, settings: Settings) -> None:
    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        await message.answer(
            "Assalomu alaykum!\n"
            "Men AI asosida zakaz xabarlarini yig'ib beradigan botman.\n"
            "Meni guruhga qo'shing va mijoz xabarlarini yuboring."
        )

    @dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    async def handle_group_message(message: Message):
        if message.from_user is None or message.from_user.is_bot:
            return

        text = message.text or message.caption or ""

        logger.info(
            "New group msg chat=%s(%s) from=%s(%s) text=%r location=%s",
            message.chat.id,
            message.chat.title,
            message.from_user.id,
            message.from_user.full_name,
            text,
            bool(message.location),
        )
        print(
            f"[MSG] chat={message.chat.id}({message.chat.title}) "
            f"from={message.from_user.id}({message.from_user.full_name}) "
            f"text={text!r} location={bool(message.location)}"
        )

        session = get_or_create_session(settings, message)
        key = get_session_key(message)

        if text:
            session.raw_messages.append(text)

        phones = extract_phones(text)
        for p in phones:
            session.phones.add(p)

        loc = extract_location_from_message(message)
        if loc:
            session.location = loc

        logger.info("Current session phones=%s", session.phones)
        logger.info("Current session location=%s", session.location)

        ai_result = await classify_text_ai(settings, text, session.raw_messages)
        role = ai_result.get("role", "UNKNOWN")
        has_addr_kw = ai_result.get("has_address_keywords", False)

        logger.info("AI result=%s", ai_result)

        if role == "PRODUCT":
            if text:
                session.product_texts.append(text)
        elif role == "COMMENT" or has_addr_kw:
            if text:
                session.comments.append(text)

        session.updated_at = datetime.now(timezone.utc)

        logger.info(
            "Session ready=%s | is_completed=%s",
            is_session_ready(session),
            session.is_completed,
        )

        if not is_session_ready(session):
            return

        finalized = finalize_session(key)
        logger.info("Finalizing session key=%s, finalized=%s", key, bool(finalized))
        if not finalized:
            return

        chat_title = message.chat.title or "Noma'lum guruh"
        user = message.from_user
        full_name = user.full_name if user.full_name else f"id={user.id}"

        phones_str = ", ".join(sorted(finalized.phones)) if finalized.phones else "—"
        comment_str = "\n".join(finalized.comments) if finalized.comments else "—"
        products_str = "\n".join(finalized.product_texts) if finalized.product_texts else "—"

        loc = finalized.location
        if loc:
            if loc["type"] == "telegram":
                lat = loc["lat"]
                lon = loc["lon"]
                loc_str = f"Telegram location\nhttps://maps.google.com/?q={lat},{lon}"
            else:
                raw = loc["raw"] or ""
                loc_str = f"{loc['type']} location: {raw}"
        else:
            loc_str = "—"

        msg_text = (
            f"🆕 Yangi zakaz\n"
            f"👥 Guruh: {chat_title}\n"
            f"👤 Mijoz: {full_name} (id: {user.id})\n\n"
            f"📞 Telefon(lar): {phones_str}\n"
            f"📍 Manzil: {loc_str}\n"
            f"💬 Izoh/comment:\n{comment_str}\n\n"
            f"☕ Mahsulot/zakaz matni:\n{products_str}"
        )

        save_order_to_json(finalized)
        logger.info("Order saved to ai_bot.json for key=%s", key)

        logger.info("Sending order message to chat=%s", message.chat.id)
        await message.answer(msg_text)

        clear_session(key)
        logger.info("Session cleared for key=%s", key)
