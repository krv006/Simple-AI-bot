# bot/handlers/order_finalize.py
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message

from ..config import Settings
from ..db import save_order_row
from ..storage import finalize_session, clear_session, save_order_to_json
from .order_utils import build_final_texts, append_dataset_line

logger = logging.getLogger(__name__)


async def auto_remove_cancel_keyboard(order_message: Message, delay: int = 30):
    """
    N sekunddan keyin inline keyboardni avtomatik olib tashlash.
    DB statusiga tegmaydi, faqat tugmani o'chiradi.
    """
    await asyncio.sleep(delay)
    try:
        await order_message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as e:
        logger.warning("Failed to auto-remove inline keyboard: %s", e)


async def finalize_and_send_after_delay(
    key: str,
    base_message: Message,
    settings: Settings,
):
    """
    Session tayyor bo'lgach ham darhol emas, 5 sekunddan keyin finalize + send qiladi.
    Shu orada kelgan qo'shimcha izoh xabarlar ham session.raw_messages ichiga tushadi.
    """
    await asyncio.sleep(5)

    finalized = finalize_session(key)
    logger.info("Delayed finalize for key=%s, finalized=%s", key, bool(finalized))
    if not finalized:
        return

    client_phones, final_products, final_comments = build_final_texts(
        finalized.raw_messages, finalized.phones
    )

    chat_title = base_message.chat.title or "Noma'lum guruh"
    user = base_message.from_user
    full_name = user.full_name if user and user.full_name else f"id={user.id}"

    phones_str = ", ".join(client_phones) if client_phones else "—"
    comment_str = "\n".join(final_comments) if final_comments else "—"
    products_str = "\n".join(final_products) if final_products else "—"

    loc = finalized.location
    if loc:
        if loc["type"] == "telegram":
            lat = loc["lat"]
            lon = loc["lon"]
            loc_str = f"Telegram location\nhttps://maps.google.com/?q={lat},{lon}"
        else:
            raw_loc = loc["raw"] or ""
            loc_str = f"{loc['type']} location: {raw_loc}"
    else:
        loc_str = "—"

    msg_text = (
        f"🆕 Yangi zakaz\n"
        f"👥 Guruhdan: {chat_title}\n"
        f"👤 Mijoz: {full_name} (id: {user.id})\n\n"
        f"📞 Telefon(lar): {phones_str}\n"
        f"📍 Manzil: {loc_str}\n"
        f"💬 Izoh/comment:\n{comment_str}\n\n"
        f"☕️ Mahsulot/zakaz matni:\n{products_str}"
    )

    # JSON log
    save_order_to_json(finalized)
    logger.info("Order saved to ai_bot.json for key=%s", key)

    # Dataset fayl (order.txt)
    append_dataset_line(
        "order.txt",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "order",
            "chat_id": base_message.chat.id,
            "chat_title": chat_title,
            "user_id": user.id,
            "user_name": full_name,
            "phones": client_phones,
            "location": finalized.location,
            "raw_messages": finalized.raw_messages,
        },
    )

    # DB ga yozamiz (status = TRUE). save_order_row dan order_id qaytishi kerak.
    order_id: Optional[int] = None
    try:
        order_id = save_order_row(
            settings=settings,
            message=base_message,
            phones=client_phones,
            order_text=products_str,
            location=finalized.location,
        )
    except Exception as e:
        logger.error("Failed to save order to Postgres: %s", e)

    reply_markup = None
    if order_id is not None:
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Buyurtmani bekor qilish",
                        callback_data=f"cancel_order:{order_id}",
                    )
                ]
            ]
        )

    target_chat_id = settings.send_group_id or base_message.chat.id
    logger.info("Sending order to target group=%s", target_chat_id)

    try:
        sent_msg = await base_message.bot.send_message(
            target_chat_id,
            msg_text,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as e:
        logger.error(
            "Failed to send order to target_chat_id=%s: %s. "
            "Falling back to source chat_id=%s",
            target_chat_id,
            e,
            base_message.chat.id,
        )
        sent_msg = await base_message.answer(msg_text, reply_markup=reply_markup)

    # 10 sekunddan keyin inline keyboardni avtomatik olib tashlash
    if reply_markup is not None:
        asyncio.create_task(auto_remove_cancel_keyboard(sent_msg, delay=10))

    clear_session(key)
    logger.info("Session cleared for key=%s", key)
