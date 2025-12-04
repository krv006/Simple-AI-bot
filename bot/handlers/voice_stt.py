# bot/handlers/voice_stt.py
import logging
from datetime import datetime, timezone
from io import BytesIO

from aiogram import Dispatcher, F
from aiogram.enums import ChatType
from aiogram.types import Message

from bot.ai.stt_uzbekvoice import stt_uzbekvoice
from bot.ai.voice_order_structured import extract_order_structured
from bot.config import Settings
from bot.storage import get_or_create_session
from bot.utils.amounts import extract_amount_from_text
from bot.utils.phones import (
    extract_phones,
    extract_spoken_phone_candidates,
    normalize_phone,
)

logger = logging.getLogger(__name__)


def register_voice_handlers(dp: Dispatcher, settings: Settings) -> None:
    @dp.message(
        F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}),
        F.voice,
    )
    async def handle_voice_message(message: Message):
        if message.from_user is None or message.from_user.is_bot:
            return

        if not getattr(settings, "uzbekvoice_api_key", None):
            await message.answer(
                "STT servisi sozlanmagan (UZBEKVOICE_API_KEY). Admin bilan bog‘laning."
            )
            return

        try:
            # 1. Voice faylni olish
            file_info = await message.bot.get_file(message.voice.file_id)
            file_path = file_info.file_path

            bio = BytesIO()
            await message.bot.download_file(file_path, bio)
            bio.seek(0)
            file_bytes = bio.read()

            # 2. Uzbekvoice STT - text
            text = await stt_uzbekvoice(
                file_bytes=file_bytes,
                api_key=settings.uzbekvoice_api_key,
                language="uz",
            )

            if not text:
                await message.answer(
                    "Golosni matnga o‘girishda xatolik bo‘ldi, keyinroq yana urinib ko‘ring."
                )
                return

            logger.info("STT text: %r", text)
            print(text)

            # 3. Sessionga yozish
            session = get_or_create_session(settings, message)
            if text:
                session.raw_messages.append(text)

            # 4. Rule-based: raqamli telefonlar
            phones_in_msg = extract_phones(text)

            # 5. Rule-based: og'zaki telefonlar
            spoken_digit_seqs = extract_spoken_phone_candidates(text)
            for seq in spoken_digit_seqs:
                p = normalize_phone(seq)
                if p and p not in phones_in_msg:
                    phones_in_msg.append(p)

            # 6. Rule-based: SUMMA
            amount_rule = extract_amount_from_text(text)
            if amount_rule is not None:
                logger.info("Extracted amount (rule) from STT: %s", amount_rule)

            # 7. LangChain structured output orqali yakuniy natijani olish
            try:
                ai_result = extract_order_structured(
                    settings,
                    text=text,
                    raw_phone_candidates=phones_in_msg,
                    raw_amount_candidates=[amount_rule] if amount_rule is not None else [],
                )
                logger.info("Structured AI result: %s", ai_result.json())
            except Exception as ai_err:
                logger.exception("Failed to run structured AI extraction: %s", ai_err)
                # fallback: ai ishlamasa, rule-based natijaga qolamiz
                ai_result = None

            # 8. Yakuniy telefon va summani tanlash
            final_phones = []
            final_amount = None

            if ai_result is not None and ai_result.is_order:
                # AI bergan natijani asosiy deb olamiz
                final_phones = ai_result.phone_numbers or []
                final_amount = ai_result.amount if ai_result.amount is not None else amount_rule
                final_comment = ai_result.comment
            else:
                # fallback: AI yoq / xatolik / order emas – faqat rule-based
                final_phones = phones_in_msg
                final_amount = amount_rule
                final_comment = text  # yoki bo'sh

            for p in final_phones:
                session.phones.add(p)

            if final_amount is not None:
                session.amount = final_amount

            session.updated_at = datetime.now(timezone.utc)

            reply_text = f"🎤 Golosdan olingan matn:\n\n{text}"

            if final_amount is not None:
                reply_text += f"\n\n💰 Summa: {final_amount:,} so'm".replace(",", " ")

            if final_phones:
                reply_text += "\n\n📞 Telefon(lar):\n" + "\n".join(final_phones)

            if ai_result is not None and ai_result.comment:
                reply_text += f"\n\n💬 Izoh (AI):\n{ai_result.comment}"

            reply_text += (
                "\n\nℹ️ Agar summa yoki telefon xato bo‘lgan bo‘lsa, "
                "keyingi golos yoki matnli xabarda to‘g‘rilab aytsangiz, "
                "bot avtomatik yangilaydi. Manzilni esa location qilib yuboring."
            )

            await message.answer(reply_text)

            # 11. Location so'rash logikasi – eski holicha
            low = text.lower()
            has_digits = any(ch.isdigit() for ch in text)
            money_kw = [
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
            has_money_kw = any(kw in low for kw in money_kw)

            has_amount_candidate = (
                    has_digits or has_money_kw or (final_amount is not None)
            )
            has_phone_candidate = bool(session.phones)

            logger.info(
                "Voice session updated: phones=%s, location=%s, "
                "has_amount_candidate=%s, has_phone_candidate=%s",
                session.phones,
                session.location,
                has_amount_candidate,
                has_phone_candidate,
            )

            if (has_amount_candidate or has_phone_candidate) and session.location is None:
                await message.answer(
                    "✅ Zakaz ma'lumotlari qabul qilindi (telefon/summa).\n"
                    "📍 Iltimos, endi manzilni location ko‘rinishida yuboring."
                )

        except Exception as e:
            logger.exception("Error while processing voice message: %s", e)
            await message.answer("Golosni qayta ishlashda kutilmagan xatolik yuz berdi.")
