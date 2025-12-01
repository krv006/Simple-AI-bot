# bot/handlers/order_manual.py
import logging
from typing import Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)


async def start_manual_order_after_cancel(
        callback: CallbackQuery,
        from_order_id: Optional[int],
) -> None:
    """
    Buyurtma bekor qilingandan keyin foydalanuvchi
    "✅ Ha, yangi zakaz" tugmasini bossagina chaqiriladi.

    Hech qanday step-by-step rejim yo‘q, faqat foydalanuvchiga
    yangi zakazni qanday yuborishini tushuntiramiz.
    Keyingi barcha xabarlar odatdagi AI flow (handle_group_message)
    orqali qayta ishlanadi.
    """
    if not callback.from_user:
        return

    try:
        await callback.message.reply(
            "Yangi zakaz yaratamiz.\n"
            "Endi iltimos, yangi zakaz uchun xabar(lar)ni odatdagidek yuboring:\n"
            "• mijoz ismi (ixtiyoriy)\n"
            "• telefon raqam(lar)i\n"
            "• summa / to'lov shartlari (masalan: 277 000, 25 min, bezkredit)\n"
            "• manzil (Telegram lokatsiya yoki matn ko'rinishida)\n\n"
        )
    except TelegramBadRequest as e:
        logger.error("Failed to send manual instruction after cancel: %s", e)
