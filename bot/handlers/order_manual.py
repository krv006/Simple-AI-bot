# bot/handlers/order_manual.py
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Tuple, Any, Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from .order_finalize import auto_remove_cancel_keyboard
from .order_utils import append_dataset_line
from ..config import Settings
from ..db import save_order_row
from ..utils.locations import extract_location_from_message
from ..utils.phones import extract_phones

logger = logging.getLogger(__name__)

# (chat_id, user_id) -> {"step": "...", "from_order_id": int | None, "data": {...}}
manual_order_state: Dict[Tuple[int, int], Dict[str, Any]] = {}


async def handle_manual_order_step(
        message: Message,
        settings: Settings,
) -> bool:
    """
    Bekor qilingan zakazdan keyin qo'lda yangi zakaz yaratish bosqichlari:
    - amount
    - location
    - phones
    """
    if not message.from_user:
        return False

    key = (message.chat.id, message.from_user.id)
    state = manual_order_state.get(key)
    if not state:
        return False

    step = state.get("step")
    data = state.setdefault("data", {})
    text = (message.text or "").strip()

    # 1) SUMMA
    if step == "amount":
        if not text:
            await message.reply(
                "Iltimos, zakaz summasini matn ko'rinishida yuboring (masalan: 150 000)."
            )
            return True

        data["amount"] = text
        state["step"] = "location"

        await message.reply(
            "Rahmat.\n"
            "2️⃣ Endi manzilni yuboring.\n"
            "Telegram lokatsiya yuborsangiz ham bo‘ladi, matn ko‘rinishida ham yozish mumkin."
        )
        return True

    # 2) LOCATION
    if step == "location":
        loc = extract_location_from_message(message)
        loc_text: Optional[str] = None

        if loc:
            if loc["type"] == "telegram":
                lat = loc["lat"]
                lon = loc["lon"]
                loc_text = f"Telegram location\nhttps://maps.google.com/?q={lat},{lon}"
            else:
                raw = loc.get("raw") or ""
                loc_text = raw or "—"
        else:
            # Lokatsiya yo'q, matn ko'rinishida bo'lsa – text'dan olamiz
            if text:
                loc = {
                    "type": "text",
                    "raw": text,
                }
                loc_text = text
            else:
                await message.reply(
                    "Manzilni lokatsiya yoki matn ko‘rinishida yuboring."
                )
                return True

        data["location"] = loc
        data["location_text"] = loc_text
        state["step"] = "phones"

        await message.reply(
            "3️⃣ Endi mijoz telefon raqamini yuboring (+998...).\n"
            "Bir nechta bo‘lsa, vergul bilan ajratib yozing."
        )
        return True

    # 3) PHONES
    if step == "phones":
        phones = extract_phones(message.text or "")
        if not phones:
            await message.reply(
                "Kamida bitta telefon raqam yuboring (+998...)."
            )
            return True

        data["phones"] = phones

        # Yangi buyurtmani DB ga yozamiz
        try:
            order_id = save_order_row(
                settings=settings,
                message=message,
                phones=phones,
                order_text=f"Qo'lda yaratilgan zakaz. Summa: {data.get('amount')}",
                location=data.get("location"),
            )
        except Exception as e:
            logger.error("Failed to save manual order after cancel: %s", e)
            await message.reply("Yangi buyurtmani saqlashda xato bo‘ldi.")
            manual_order_state.pop(key, None)
            return True

        chat_title = message.chat.title or "Noma'lum guruh"
        user = message.from_user
        full_name = user.full_name if user and user.full_name else f"id={user.id}"

        phones_str = ", ".join(phones) if phones else "—"
        loc_text = data.get("location_text") or "—"
        amount = data.get("amount") or "—"
        from_order_id = state.get("from_order_id")

        header_line = "🆕 Yangi zakaz (qo'lda)"
        if order_id is not None:
            header_line += f" (ID: {order_id})"

        client_line = f"👤 Mijoz: {full_name} (id: {user.id})"

        msg_text = (
            f"{header_line}\n"
            f"👥 Guruhdan: {chat_title}\n"
            f"{client_line}\n\n"
            f"📞 Telefon(lar): {phones_str}\n"
            f"📍 Manzil: {loc_text}\n"
            f"💰 Summa: {amount}\n"
            f"💬 Izoh/comment:\n—\n\n"
            f"☕️ Mahsulot/zakaz matni:\n"
            f"Qo'lda yaratilgan zakaz (bekor qilingan eski ID: {from_order_id})"
        )

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

        target_chat_id = settings.send_group_id or message.chat.id

        try:
            sent_msg = await message.bot.send_message(
                target_chat_id,
                msg_text,
                reply_markup=reply_markup,
            )
        except TelegramBadRequest as e:
            logger.error(
                "Failed to send manual order to target chat=%s: %s",
                target_chat_id,
                e,
            )
            sent_msg = await message.answer(msg_text, reply_markup=reply_markup)

        if reply_markup is not None:
            asyncio.create_task(
                auto_remove_cancel_keyboard(sent_msg, delay=30)
            )

        append_dataset_line(
            "orders_manual.txt",
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "manual_after_cancel",
                "from_order_id": from_order_id,
                "new_order_id": order_id,
                "chat_id": message.chat.id,
                "chat_title": chat_title,
                "user_id": user.id,
                "user_name": full_name,
                "amount": amount,
                "phones": phones,
                "location": data.get("location"),
                "location_text": loc_text,
            },
        )

        await message.reply("✅ Yangi buyurtma yaratildi.")
        manual_order_state.pop(key, None)
        return True

    return False


async def start_manual_order_after_cancel(
        callback: CallbackQuery,
        from_order_id: Optional[int],
) -> None:

    if not callback.from_user:
        return

    key = (callback.message.chat.id, callback.from_user.id)
    manual_order_state[key] = {
        "step": "amount",
        "from_order_id": from_order_id,
        "data": {},
    }

    try:
        await callback.message.reply(
            "Yangi zakaz yaratamiz.\n"
            "1️⃣ Iltimos, zakaz summasini yozing (masalan: 150 000)."
        )
    except TelegramBadRequest:
        pass
