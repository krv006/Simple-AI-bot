# bot/handlers/order_reply_update.py
import logging
from datetime import datetime, timezone
from typing import Optional, List

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from .order_utils import parse_order_message_text, append_dataset_line
from ..config import Settings
from ..db import cancel_order_row, save_order_row
from ..utils.locations import extract_location_from_message
from ..utils.phones import extract_phones

logger = logging.getLogger(__name__)


async def handle_order_reply_update(
        message: Message,
        settings: Settings,
) -> bool:
    """
    Buyurtma xabariga reply qilingan xabarni qayta ishlash.
    Hozircha:
    - lokatsiya o'zgarsa
    - telefon raqam(lar) o'zgarsa
    eski buyurtmani BEKOR qilamiz va yangisini yaratamiz.
    """
    reply_msg = message.reply_to_message
    if not reply_msg or not reply_msg.text:
        return False

    # Faqat zakaz xabarlariga ishlasin:
    if not reply_msg.text.startswith("🆕 Yangi zakaz"):
        return False

    parsed = parse_order_message_text(reply_msg.text)
    if not parsed:
        return False

    order_id = parsed["order_id"]
    if not order_id:
        return False

    old_phones: List[str] = parsed["phones"] or []
    old_location_text: Optional[str] = parsed.get("location_text")

    new_loc = extract_location_from_message(message)
    reply_text = message.text or message.caption or ""
    reply_phones = extract_phones(reply_text)

    old_phones_set = set(old_phones)
    reply_phones_set = set(reply_phones)

    phones_changed = bool(reply_phones_set) and (reply_phones_set != old_phones_set)
    has_new_loc = bool(new_loc)

    if not has_new_loc and not phones_changed:
        return False

    logger.info(
        "Order reply update detected: order_id=%s, new_loc=%s, phones_changed=%s",
        order_id,
        new_loc,
        phones_changed,
    )

    try:
        cancelled = cancel_order_row(settings=settings, order_id=order_id)
    except Exception as e:
        logger.error("Failed to cancel order_id=%s on update: %s", order_id, e)
        await message.reply(
            "Eski buyurtmani bekor qilishda xatolik yuz berdi."
        )
        return True

    if not cancelled:
        await message.reply(
            "Eski buyurtma topilmadi yoki allaqachon bekor qilingan."
        )
        return True

    # Eski xabarni vizual belgilab qo'yamiz
    reason_parts = []
    if has_new_loc:
        reason_parts.append("lokatsiya o‘zgartirildi")
    if phones_changed:
        reason_parts.append("telefon raqami(lar) o‘zgartirildi")

    reason_text = ", ".join(reason_parts) if reason_parts else "ma'lumotlar yangilandi"

    try:
        await reply_msg.edit_text(
            reply_msg.text
            + f"\n\n❌ Buyurtma bekor qilingan ({reason_text})."
        )
    except TelegramBadRequest:
        pass

    # Eski xabardan product/comment va boshqa maydonlarni olib qolamiz
    products_str = parsed["products"] or ""
    comments_str = parsed["comments"] or ""
    chat_title = parsed["chat_title"]
    client_name = parsed["client_name"]
    client_id = parsed["client_id"]

    if phones_changed:
        phones = sorted(reply_phones_set)
    else:
        phones = old_phones

    phones_str = ", ".join(phones) if phones else "—"
    comment_str = comments_str or "—"

    if has_new_loc:
        loc = new_loc
        if loc["type"] == "telegram":
            lat = loc["lat"]
            lon = loc["lon"]
            loc_str = f"Telegram location\nhttps://maps.google.com/?q={lat},{lon}"
        else:
            raw_loc = loc["raw"] or ""
            loc_str = f"{loc['type']} location: {raw_loc}"
    else:
        if old_location_text and old_location_text != "—":
            loc_str = old_location_text
            loc = {
                "type": "text",
                "raw": old_location_text,
            }
        else:
            loc_str = "—"
            loc = None

    # Yangi buyurtmani DB ga yozamiz
    new_order_id = None
    try:
        new_order_id = save_order_row(
            settings=settings,
            message=message,  # update so'ragan foydalanuvchi sifatida yozamiz
            phones=phones,
            order_text=products_str,
            location=loc,
        )
    except Exception as e:
        logger.error("Failed to save updated order to Postgres: %s", e)
        await message.reply(
            "Yangilangan ma'lumotlar bilan buyurtmani saqlashda xato bo‘ldi."
        )
        return True

    header_line = "🆕 Yangi zakaz (yangilangan)"
    if new_order_id is not None:
        header_line += f" (ID: {new_order_id})"

    # Agar eski xabardan mijozni o‘qib olgan bo‘lsak – o‘shanini ishlatamiz
    if client_name and client_id:
        client_line = f"👤 Mijoz: {client_name} (id: {client_id})"
    else:
        user = message.from_user
        full_name = (
            user.full_name if user and user.full_name else f"id={user.id}"
        )
        client_line = f"👤 Mijoz: {full_name} (id: {user.id})"

    msg_text = (
        f"{header_line}\n"
        f"👥 Guruhdan: {chat_title or (message.chat.title or "Noma'lum guruh")}\n"
        f"{client_line}\n\n"
        f"📞 Telefon(lar): {phones_str}\n"
        f"📍 Manzil: {loc_str}\n"
        f"💬 Izoh/comment:\n{comment_str}\n\n"
        f"☕️ Mahsulot/zakaz matni:\n{products_str}"
    )

    reply_markup = None
    if new_order_id is not None:
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="❌ Buyurtmani bekor qilish",
                        callback_data=f"cancel_order:{new_order_id}",
                    )
                ]
            ]
        )

    target_chat_id = settings.send_group_id or message.chat.id

    try:
        sent_msg = await message.bot.send_message(
            target_chat_id,
            msg_text,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as e:
        logger.error(
            "Failed to send updated order to target chat=%s: %s",
            target_chat_id,
            e,
        )
        sent_msg = await message.answer(msg_text, reply_markup=reply_markup)

    # Dataset uchun ham yozib qo'yamiz
    append_dataset_line(
        "order_updates.txt",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "order_update",
            "old_order_id": order_id,
            "new_order_id": new_order_id,
            "chat_id": message.chat.id,
            "user_id": message.from_user.id if message.from_user else None,
            "location": loc,
            "phones_old": old_phones,
            "phones_new": phones,
            "location_updated": has_new_loc,
            "phones_updated": phones_changed,
        },
    )

    return True
