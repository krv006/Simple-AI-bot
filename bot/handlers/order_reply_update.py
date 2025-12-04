# bot/handlers/order_reply_update.py
import logging
from datetime import datetime, timezone
from typing import Optional, List

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from .order_utils import parse_order_message_text, append_dataset_line
from ..config import Settings
from ..db import update_order_row  # YANGI: eski orderni update qilish uchun
from ..utils.amounts import extract_amount_from_text  # agar summa ham o'zgarsa
from ..utils.locations import extract_location_from_message
from ..utils.phones import extract_phones

logger = logging.getLogger(__name__)


async def handle_order_reply_update(
        message: Message,
        settings: Settings,
) -> bool:
    """
    Buyurtma xabariga reply qilingan xabarni qayta ishlash.

    YANGI LOJIKA:
    - lokatsiya o'zgarsa
    - telefon raqam(lar) o'zgarsa
    - (ixtiyoriy) summa o'zgarsa

    eski buyurtmani BEKOR QILMAYMIZ, YANGI ORDER YARATMAYMIZ.
    O'sha bir xil order_id bo'yicha DB yozuvni UPDATE qilamiz va
    Telegram xabarning matnini edit qilamiz.
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

    # Eski summa – mavjud zakaz xabaridan o'qiymiz (agar kerak bo'lsa)
    old_amount = extract_amount_from_text(reply_msg.text)

    # Reply xabardan yangi ma'lumotlarni olish
    new_loc = extract_location_from_message(message)
    reply_text = message.text or message.caption or ""
    reply_phones = extract_phones(reply_text)
    new_amount = extract_amount_from_text(reply_text)

    old_phones_set = set(old_phones)
    reply_phones_set = set(reply_phones)

    phones_changed = bool(reply_phones_set) and (reply_phones_set != old_phones_set)
    has_new_loc = bool(new_loc)
    amount_changed = (new_amount is not None) and (new_amount != old_amount)

    # Hech narsa o'zgarmasa, update qilmaymiz
    if not has_new_loc and not phones_changed and not amount_changed:
        return False

    logger.info(
        "Order reply update detected: order_id=%s, new_loc=%s, "
        "phones_changed=%s, amount_changed=%s (old_amount=%s, new_amount=%s)",
        order_id,
        new_loc,
        phones_changed,
        amount_changed,
        old_amount,
        new_amount,
    )

    # Eski xabarni vizual belgilash uchun reason text
    reason_parts = []
    if has_new_loc:
        reason_parts.append("lokatsiya o‘zgartirildi")
    if phones_changed:
        reason_parts.append("telefon raqami(lar) o‘zgartirildi")
    if amount_changed:
        reason_parts.append("summa o‘zgartirildi")

    reason_text = ", ".join(reason_parts) if reason_parts else "ma'lumotlar yangilandi"

    # Eski xabarni oxiriga izoh qo'shamiz (lekin sarlavhani to'liq almashtirmasdan)
    try:
        await reply_msg.edit_text(
            reply_msg.text
            + f"\n\n♻️ Buyurtma ma'lumotlari yangilandi ({reason_text})."
        )
    except TelegramBadRequest:
        # Agar eski matn juda uzun bo'lsa yoki HTML xatolik bo'lsa – shunchaki e'tibor bermaymiz
        logger.warning("Failed to append update reason to original message", exc_info=True)

    # Eski xabardan product/comment va boshqa maydonlarni olib qolamiz
    products_str = parsed["products"] or ""
    comments_str = parsed["comments"] or ""
    chat_title = parsed["chat_title"]
    client_name = parsed["client_name"]
    client_id = parsed["client_id"]

    # Yangilangan telefon ro'yxati
    if phones_changed:
        phones = sorted(reply_phones_set)
    else:
        phones = old_phones

    phones_str = ", ".join(phones) if phones else "—"
    comment_str = comments_str or "—"

    # Location yangilash
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

    # Summa uchun ko'rsatish va DB ga berish
    if new_amount is not None:
        amount_to_show = new_amount
    else:
        amount_to_show = old_amount

    if amount_to_show is not None:
        try:
            amount_int = int(amount_to_show)
        except (TypeError, ValueError):
            amount_int = None
    else:
        amount_int = None

    if amount_int is not None:
        amount_str = f"{amount_int:,}".replace(",", " ")
        amount_line = f"💰 Summa: {amount_str} so'm"
    else:
        amount_line = "💰 Summa: —"

    # DBdagi o'sha order_id bo'yicha yozuvni UPDATE qilamiz
    try:
        updated = update_order_row(
            settings=settings,
            order_id=order_id,
            phones=phones,
            order_text=products_str,
            location=loc,
            # agar DB'da summa ustuni bo'lsa:
            amount=amount_int,
        )
    except Exception as e:
        logger.error("Failed to update order_id=%s: %s", order_id, e)
        await message.reply(
            "Buyurtma ma'lumotlarini yangilashda xatolik yuz berdi."
        )
        return True

    if not updated:
        await message.reply(
            "Buyurtma topilmadi yoki yangilab bo'lmadi."
        )
        return True

    # Telegramdagi asosiy zakaz xabarini yangilangan ma'lumot bilan to'liq qayta yozamiz
    header_line = parsed.get("header_line")
    if not header_line:
        # Fallback – agar parse_order_message_text header_line qaytarmasa:
        header_line = f"🆕 Yangi zakaz (ID: {order_id})"

    # Mijoz satri
    if client_name and client_id:
        client_line = f"👤 Mijoz: {client_name} (id: {client_id})"
    else:
        user = message.from_user
        full_name = (
            user.full_name if user and user.full_name else f"id={user.id}"
        )
        client_line = f"👤 Mijoz: {full_name} (id: {user.id})"

    group_title = chat_title or (message.chat.title or "Noma'lum guruh")

    new_msg_text = (
        f"{header_line}\n"
        f"👥 Guruhdan: {group_title}\n"
        f"{client_line}\n\n"
        f"📞 Telefon(lar): {phones_str}\n"
        f"{amount_line}\n"
        f"📍 Manzil: {loc_str}\n"
        f"💬 Izoh/comment:\n{comment_str}\n\n"
        f"☕️ Mahsulot/zakaz matni:\n{products_str}"
    )

    # Inline knopkalarni saqlab qolamiz (cancel_order:{order_id})
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

    try:
        await reply_msg.edit_text(new_msg_text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        logger.error(
            "Failed to edit original order message for order_id=%s: %s",
            order_id,
            e,
        )
        # Agar edit ishlamasa, hech bo'lmasa yangi xabar yuboramiz,
        # lekin baribir o'sha order_id haqida gap ketadi (yangi ID yaratmaymiz)
        await message.answer(new_msg_text, reply_markup=reply_markup)

    # Dataset uchun ham yozib qo'yamiz
    append_dataset_line(
        "order_updates.txt",
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "order_update",
            "order_id": order_id,
            "chat_id": message.chat.id,
            "user_id": message.from_user.id if message.from_user else None,
            "location": loc,
            "phones_old": old_phones,
            "phones_new": phones,
            "location_updated": has_new_loc,
            "phones_updated": phones_changed,
            "amount_old": old_amount,
            "amount_new": new_amount,
            "amount_updated": amount_changed,
        },
    )

    return True
