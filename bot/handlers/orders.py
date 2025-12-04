# bot/handlers/order.py
import asyncio
import logging
from datetime import datetime, timezone
from io import BytesIO

from aiogram import Dispatcher, F
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from bot.ai.status_intent import is_status_question
from bot.ai.stt_uzbekvoice import stt_uzbekvoice
from bot.utils.read_file import read_text_file
from .order_finalize import finalize_and_send_after_delay
from .order_manual import start_manual_order_after_cancel
from .order_reply_update import handle_order_reply_update
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

    # GROUP MESSAGE handler (text, caption, voice ham shu yerga tushadi)
    @dp.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
    async def handle_group_message(message: Message):
        if message.from_user is None or message.from_user.is_bot:
            return

        # 1) Avval: agar eski zakaz xabariga reply bo'lsa – update logika (loc + phone)
        if message.reply_to_message:
            handled = await handle_order_reply_update(message, settings)
            if handled:
                return

        # === 1-qadam: textni tayyorlash (voice bo'lsa STT) ===
        text: str = ""

        if message.voice:
            # Golos orqali kelgan bo‘lsa, Uzbekvoice bilan STT qilamiz
            if not settings.uzbekvoice_api_key:
                await message.reply(
                    "Golosni o‘qish servisi sozlanmagan (UZBEKVOICE_API_KEY). Admin bilan bog‘laning."
                )
                return

            try:
                file_info = await message.bot.get_file(message.voice.file_id)
                file_path = file_info.file_path

                bio = BytesIO()
                await message.bot.download_file(file_path, bio)
                bio.seek(0)
                file_bytes = bio.read()

                stt_text = await stt_uzbekvoice(
                    file_bytes=file_bytes,
                    api_key=settings.uzbekvoice_api_key,
                    language="uz",
                )

                if stt_text:
                    text = stt_text
                else:
                    # fallback – agar caption bo‘lsa, shundan foydalanamiz
                    text = message.caption or ""
                    if not text.strip():
                        await message.reply(
                            "Golosni matnga o‘girishda xatolik bo‘ldi, keyinroq qayta urinib ko‘ring."
                        )
                        return

                # debug uchun xohlasangiz:
                # await message.reply(f"🎤 Golosdan olingan matn:\n\n{text}")

            except Exception as e:
                logger.exception("Error while processing voice STT: %s", e)
                await message.reply(
                    "Golosni qayta ishlashda kutilmagan xatolik yuz berdi."
                )
                return
        else:
            # Oddiy holat: text yoki caption
            text = message.text or message.caption or ""

        logger.info(
            "New group msg chat=%s(%s) from=%s(%s) text=%r location=%s voice=%s",
            message.chat.id,
            message.chat.title,
            message.from_user.id,
            message.from_user.full_name,
            text,
            bool(message.location),
            bool(message.voice),
        )
        print(
            f"[MSG] chat={message.chat.id}({message.chat.title}) "
            f"from={message.from_user.id}({message.from_user.full_name}) "
            f"text={text!r} location={bool(message.location)} voice={bool(message.voice)}"
        )

        # Session olish
        session = get_or_create_session(settings, message)
        key = get_session_key(message)

        if session.is_completed:
            logger.info("Session already completed for key=%s, skipping.", key)
            return

        if text:
            session.raw_messages.append(text)

        # Telefon raqamlar
        had_phones_before = bool(session.phones)
        phones_in_msg = extract_phones(text)
        for p in phones_in_msg:
            session.phones.add(p)
        phones_new = bool(session.phones) and not had_phones_before

        # Location
        had_location_before = session.location is not None
        loc = extract_location_from_message(message)
        just_got_location = False
        if loc:
            session.location = loc
            if not had_location_before:
                just_got_location = True

        logger.info("Current session phones=%s", session.phones)
        logger.info("Current session location=%s", session.location)

        # === YANGI: golosdan keyin location so‘rash ===
        if (
                message.voice  # aynan golosdan keyin
                and session.phones  # telefon bor
                and session.location is None  # hali location yo'q
        ):
            try:
                await message.reply(
                    "✅ Zakaz ma'lumotlari qabul qilindi.\n"
                    "📍 Iltimos, endi manzilni location ko‘rinishida yuboring."
                )
            except TelegramBadRequest:
                pass
            # Pipeline davom etadi, faqat userga eslatma.

        # === AI klassifikatsiya (LangChain Structured Output) ===
        ai_result = await classify_text_ai(settings, text, session.raw_messages)
        # kutiladigan maydonlar:
        # role: str
        # has_address_keywords: bool
        # is_order_related: bool
        # reason: str
        # order_probability: float
        # source: str
        # amount: Optional[int]  # LangChain structured output dan

        role = ai_result.get("role", "UNKNOWN")
        has_addr_kw = ai_result.get("has_address_keywords", False)
        is_order_related = ai_result.get("is_order_related", False)
        reason = ai_result.get("reason") or ""
        order_prob = ai_result.get("order_probability", None)
        source = ai_result.get("source", "UNKNOWN")
        amount = ai_result.get("amount")

        # Agar AI summa topgan bo‘lsa – sessiyaga yozib qo‘yamiz
        if amount is not None:
            try:
                # agar oldin summa bo'lmasa yoki 0 bo'lsa yangilaymiz
                if getattr(session, "amount", None) in (None, 0):
                    session.amount = int(amount)
            except Exception:
                # har ehtimolga qarshi, sessiya modeli o‘zgarmagan bo‘lsa ham bot yiqilmasin
                logger.warning("Failed to set session.amount from ai_result: %r", amount)

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

            if amount is not None:
                debug_text += f"- AI summa: {amount}\n"

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
                        "amount": amount,
                    },
                },
            )

        # === STATUS so'rovini ajratish (telefon/location yo'q bo'lsa) ===
        if not phones_in_msg and not message.location and text.strip():
            is_status = await is_status_question(
                settings,
                text,
                session.raw_messages,
            )
            logger.info(
                "Status intent: text=%r -> is_status=%s",
                text,
                is_status,
            )

            if is_status:
                status_text = read_text_file("bot/a.txt")
                logger.info(
                    "Status so'rovi (AI) aniqlandi, a.txt javob qaytaryapman. "
                    "chat=%s(%s) from=%s(%s) text=%r",
                    message.chat.id,
                    message.chat.title,
                    message.from_user.id,
                    message.from_user.full_name,
                    text,
                )
                await message.reply(status_text)
                return

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

        # --- Butun sessiya bo‘yicha summa / summa kandidati bor-yo‘qligini tekshiramiz ---
        all_text = " ".join(session.raw_messages).lower()
        has_digits_all = any(ch.isdigit() for ch in all_text)
        money_kw_all = [
            "summa",
            "ming",
            "min",
            "мин",
            "минг",
            "сум",
            "сом",
            "тыс",
            "so'm",
            "som",
        ]
        has_money_kw_all = any(kw in all_text for kw in money_kw_all)
        has_amount_candidate_all = has_digits_all or has_money_kw_all

        ready_base = is_session_ready(session)

        # Summa AI dan yoki matndan bo'lishi mumkin, shuning uchun
        # base_ready + (location + summa kandidati) kombinatsiyasini ham hisobga olamiz
        ready = ready_base or (
                session.location is not None and has_amount_candidate_all
        )

        logger.info(
            "Session ready=%s (base=%s) | is_completed=%s | just_got_location=%s | "
            "phones_new=%s | has_product_candidate=%s | has_amount_candidate_all=%s",
            ready,
            ready_base,
            session.is_completed,
            just_got_location,
            phones_new,
            has_product_candidate,
            has_amount_candidate_all,
        )

        if not ready or session.is_completed:
            return

        should_finalize = (
                just_got_location
                or role == "PRODUCT"
                or has_addr_kw
                or phones_new
                or has_product_candidate
                or has_amount_candidate_all
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
            await callback.answer(
                "Bekor qilishda xatolik yuz berdi.", show_alert=True
            )
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

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Ha, yangi zakaz",
                        callback_data=f"new_after_cancel_yes:{order_id}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Yo'q",
                        callback_data=f"new_after_cancel_no:{order_id}",
                    ),
                ]
            ]
        )

        try:
            await callback.message.reply(
                "❌ Buyurtma bekor qilindi.\n"
                "Yangi buyurtma yaratishni xohlaysizmi?",
                reply_markup=kb,
            )
        except TelegramBadRequest:
            pass

        await callback.answer()

    @dp.callback_query(F.data.startswith("new_after_cancel_no:"))
    async def handle_new_after_cancel_no(callback: CallbackQuery):
        await callback.answer()
        try:
            await callback.message.reply("Yaxshi, ishlaringizga omad!")
        except TelegramBadRequest:
            pass

    @dp.callback_query(F.data.startswith("new_after_cancel_yes:"))
    async def handle_new_after_cancel_yes(callback: CallbackQuery):
        data = callback.data or ""
        try:
            _, raw_id = data.split(":", 1)
            from_order_id = int(raw_id)
        except Exception:
            from_order_id = None

        await callback.answer()
        await start_manual_order_after_cancel(callback, from_order_id)
