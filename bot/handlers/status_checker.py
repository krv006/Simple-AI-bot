# bot/handlers/status_checker.py
import logging

from aiogram import Router, F
from aiogram.types import Message

from bot.utils.read_file import read_text_file
from bot.ai.status_intent import is_status_question

logger = logging.getLogger(__name__)

router = Router()


@router.message(F.text)
async def order_status_any_message(message: Message):
    """
    Har qanday text xabar uchun ishlaydi.
    AI orqali tekshiradi: bu zakaz holatini/statusini so'rovchi xabar bo'ladimi?
    Agar ha bo'lsa -> bot/a.txt dagi matnni javob qiladi.
    """

    user_text = (message.text or "").strip()
    if not user_text:
        return

    logger.info(
        "Status checker: got message chat=%s(%s) from=%s(%s) text=%r",
        message.chat.id,
        getattr(message.chat, "title", ""),
        message.from_user.id if message.from_user else None,
        message.from_user.username if message.from_user else None,
        user_text,
    )

    # Kontekst: agar reply bo'lsa, reply qilingan xabar matnini ham berib qo'yamiz
    context = []
    if message.reply_to_message and message.reply_to_message.text:
        context.append(message.reply_to_message.text)

    # 1) AI / rule-based orqali intent check
    is_status = await is_status_question(user_text, context)

    logger.info("Status checker: is_status_question=%s", is_status)

    if not is_status:
        # bu xabar zakaz holatini so'ramayapti – hech nima qilmaymiz
        return

    # 2) holat so'ralgan bo'lsa -> a.txt ni yuboramiz
    status_text = read_text_file("bot/a.txt")
    logger.info("Status checker: sending status text from bot/a.txt")
    await message.reply(status_text)
