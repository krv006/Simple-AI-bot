# bot/handlers/order.py
import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Dispatcher, F
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery

from .order_finalize import finalize_and_send_after_delay
from .order_utils import (
    COMMENT_KEYWORDS,
    append_dataset_line,
)
from ..ai.classifier import classify_text_ai
from ..config import Settings
from ..db import cancel_order_row
from ..storage import (
    get_or_create_session,
    get_session_key,
    is_session_ready,
)
from ..utils.locations import extract_location_from_message
from ..utils.phones import extract_phones

logger = logging.getLogger(__name__)


def register_order_handlers(dp: Dispatcher, settings: Settings) -> None:
    # /start
    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        await message.answer(
            "Assalomu alaykum!\n"
            "Men AI asosida zakaz xabarlarini yig'ib beradigan botman.\n"
            "Meni guruhga qo'shing va mijoz xabarlarini yuboring."
        )

    # GROUP MESSAGE handler
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

        if session.is_completed:
            logger.info("Session already completed for key=%s, skipping.", key)
            return

        if text:
            session.raw_messages.append(text)

        had_phones_before = bool(session.phones)
        phones_in_msg = extract_phones(text)
        for p in phones_in_msg:
            session.phones.add(p)
        phones_new = bool(session.phones) and not had_phones_before

        had_location_before = session.location is not None
        loc = extract_location_from_message(message)
        just_got_location = False
        if loc:
            session.location = loc
            if not had_location_before:
                just_got_location = True

        logger.info("Current session phones=%s", session.phones)
        logger.info("Current session location=%s", session.location)

        # === AI klassifikatsiya ===
        ai_result = await classify_text_ai(settings, text, session.raw_messages)
        role = ai_result.get("role", "UNKNOWN")
        has_addr_kw = ai_result.get("has_address_keywords", False)
        is_order_related = ai_result.get("is_order_related", False)
        reason = ai_result.get("reason") or ""
        order_prob = ai_result.get("order_probability", None)
        source = ai_result.get("source", "UNKNOWN")

        logger.info("AI result=%s", ai_result)

        # === AI_CHECK GURUHIGA LOG ===
        if settings.ai_check_group_id:
            src_chat_title = message.chat.title or str(message.chat.id)
            user = message.from_user
            full_name = (
                user.full_name if (user and user.full_name) else f"id={user.id}"
            )

            is_order_txt = "Ha" if is_order_related else "Yo'q"
            has_addr_txt = "Ha" if has_addr_kw else "Yo'q"

            debug_text = (
                "🤖 AI CHECK\n"
                f"👥 Guruh: {src_chat_title}\n"
                f"👤 User: {full_name} (id: {user.id})\n\n"
                f"📩 Xabar:\n{text}\n\n"
                "AI natijasi:\n"
                f"- orderga aloqador: {is_order_txt}\n"
                f"- role: {role}\n"
                f"- manzil kalit so'zlari: {has_addr_txt}\n"
                f"- manba: {source}\n"
            )

            if isinstance(order_prob, (int, float)):
                debug_text += f"- order ehtimoli: {order_prob:.2f}\n"

            if reason:
                debug_text += f"\nSabab:\n{reason}"

            try:
                await message.bot.send_message(
                    settings.ai_check_group_id, debug_text
                )
            except TelegramBadRequest as e:
                logger.error(
                    "Failed to send AI_CHECK log to ai_check_group_id=%s: %s",
                    settings.ai_check_group_id,
                    e,
                )

            append_dataset_line(
                "ai_check.txt",
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "chat_id": message.chat.id,
                    "chat_title": src_chat_title,
                    "user_id": user.id,
                    "user_name": full_name,
                    "text": text,
                    "ai": {
                        "is_order_related": is_order_related,
                        "role": role,
                        "has_address_keywords": has_addr_kw,
                        "reason": reason,
                        "order_probability": order_prob,
                        "source": source,
                    },
                },
            )

        # === Eski fallback PRODUCT/COMMENT ===
        low = text.lower()
        has_digits = any(ch.isdigit() for ch in text)
        money_kw = ["summa", "ming", "min", "мин", "минг", "сум", "сом", "тыс"]

        has_product_candidate = bool(
            has_digits or any(kw in low for kw in money_kw)
        )

        if role == "UNKNOWN":
            if has_product_candidate:
                role = "PRODUCT"
            if any(kw in low for kw in COMMENT_KEYWORDS):
                role = "COMMENT"

        # === NON-ORDER error_group ===
        if (
                settings.error_group_id
                and not is_order_related
                and not phones_in_msg
                and not message.location
                and text.strip()
        ):
            src_chat_title = message.chat.title or str(message.chat.id)
            user = message.from_user
            full_name = (
                user.full_name if user and user.full_name else f"id={user.id}"
            )

            error_text = (
                f"👥 Guruh: {src_chat_title}\n"
                f"👤 User: {full_name} (id: {user.id})\n\n"
                f"📩 Xabar:\n{text}"
            )

            append_dataset_line(
                "errors.txt",
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "type": "error",
                    "chat_id": message.chat.id,
                    "chat_title": src_chat_title,
                    "user_id": user.id,
                    "user_name": full_name,
                    "text": text,
                },
            )

            try:
                await message.bot.send_message(
                    settings.error_group_id, error_text
                )
            except TelegramBadRequest as e:
                logger.error(
                    "Failed to send non-order message to error_group_id=%s: %s",
                    settings.error_group_id,
                    e,
                )
            return

        # === Session update ===
        session.updated_at = datetime.now(timezone.utc)

        ready = is_session_ready(session)
        logger.info(
            "Session ready=%s | is_completed=%s | just_got_location=%s | "
            "phones_new=%s | has_product_candidate=%s",
            ready,
            session.is_completed,
            just_got_location,
            phones_new,
            has_product_candidate,
        )

        if not ready or session.is_completed:
            return

        should_finalize = (
                just_got_location
                or role == "PRODUCT"
                or has_addr_kw
                or phones_new
                or has_product_candidate
        )

        if not should_finalize:
            logger.info(
                "Session is ready, but current message is not a finalize trigger."
            )
            return

        asyncio.create_task(
            finalize_and_send_after_delay(
                key=key,
                base_message=message,
                settings=settings,
            )
        )
        logger.info("Finalize scheduled with 5s delay for key=%s", key)
        return

    @dp.callback_query(F.data.startswith("cancel_order:"))
    async def handle_cancel_order(callback: CallbackQuery):

        data = callback.data or ""
        try:
            _, raw_id = data.split(":", 1)
            order_id = int(raw_id)
        except Exception:
            await callback.answer("Noto'g'ri buyurtma ID.", show_alert=True)
            return

        try:
            cancelled = cancel_order_row(settings=settings, order_id=order_id)
        except Exception as e:
            logger.error("Failed to cancel order_id=%s: %s", order_id, e)
            await callback.answer("Bekor qilishda xatolik yuz berdi.", show_alert=True)
            return

        if not cancelled:
            await callback.answer(
                "Bu buyurtma allaqachon bekor qilingan yoki topilmadi.",
                show_alert=True,
            )
            return

        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass

        try:
            await callback.message.reply("❌ Buyurtmangiz bekor qilindi.")
        except TelegramBadRequest:
            pass

        await callback.answer()
