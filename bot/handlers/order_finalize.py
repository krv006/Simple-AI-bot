# bot/handlers/order_finalize.py
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, List

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message

from ..config import Settings
from ..db import save_order_row
from ..storage import finalize_session, clear_session, save_order_to_json
from .order_utils import build_final_texts, append_dataset_line

# LangChain structured output (voice uchun yozgan funksiyani umumiy foydalanamiz)
from bot.ai.voice_order_structured import extract_order_structured

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


def _clean_products_with_structured(
    raw_lines: List[str],
    phones: List[str],
    amount: Optional[int],
    client_name: Optional[str],
) -> List[str]:
    """
    Mahsulot matnidan 'Bahodir 983373630', '277 000' kabi qatorlarni olib tashlaydi.
    Telefon va summa allaqachon alohida fieldlarda ketadi.
    """
    cleaned: List[str] = []

    # Telefon suffixlari (oxirgi 7–9 raqami) bo'yicha filtrlaymiz
    phone_suffixes: List[str] = []
    for p in phones:
        digits = "".join(ch for ch in p if ch.isdigit())
        if len(digits) >= 7:
            phone_suffixes.append(digits[-7:])

    amount_digits = None
    if amount is not None:
        amount_digits = "".join(ch for ch in str(amount) if ch.isdigit())

    for line in raw_lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # raqamlar
        digits_in_line = "".join(ch for ch in line_stripped if ch.isdigit())

        skip = False

        # 1) Agar qatorda telefon raqam bo‘lsa – tashlab yuboramiz
        if digits_in_line:
            for suf in phone_suffixes:
                if suf and suf in digits_in_line:
                    skip = True
                    break

        # 2) Agar qatorda summa bo'lsa – tashlab yuboramiz
        if not skip and amount_digits and amount_digits in digits_in_line:
            skip = True

        # 3) Agar qatorda faqat ism bo'lsa (Bahodir) va client_name shu bo'lsa – tashlab yuboramiz
        if (
            not skip
            and client_name
            and line_stripped.lower().startswith(client_name.lower())
        ):
            skip = True

        if skip:
            continue

        cleaned.append(line_stripped)

    return cleaned


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

    # build_final_texts – eski rule-based ajratish (phones/products/comments)
    client_phones, final_products, final_comments = build_final_texts(
        finalized.raw_messages, finalized.phones
    )

    chat_title = base_message.chat.title or "Noma'lum guruh"
    user = base_message.from_user
    full_name = user.full_name if user and user.full_name else f"id={user.id}"

    # --- LangChain structured output orqali yakuniy strukturani chiqaramiz ---
    text_for_ai = "\n".join(finalized.raw_messages)

    raw_phone_candidates = list(finalized.phones) if finalized.phones else client_phones
    raw_amount_candidates = []
    session_amount = getattr(finalized, "amount", None)
    if session_amount is not None:
        raw_amount_candidates.append(session_amount)

    struct = None
    client_name_parsed: Optional[str] = None
    final_amount: Optional[int] = session_amount

    try:
        struct = extract_order_structured(
            settings,
            text=text_for_ai,
            raw_phone_candidates=raw_phone_candidates,
            raw_amount_candidates=raw_amount_candidates,
        )
        logger.info("Structured order result in finalize: %s", struct.json())
    except Exception as e:
        logger.exception("Failed to run structured order extraction in finalize: %s", e)
        struct = None

    # Agar AI muvaffaqiyatli bo'lsa – basic maydonlarni undan olamiz
    if struct is not None and getattr(struct, "is_order", False):
        # Telefonlar
        if struct.phone_numbers:
            client_phones = struct.phone_numbers

        # Summa
        if struct.amount is not None:
            final_amount = struct.amount

        # Ism (customer_name / client_name kabi maydonlardan izlaymiz)
        client_name_parsed = (
            getattr(struct, "customer_name", None)
            or getattr(struct, "client_name", None)
            or None
        )

        # Izoh/comment
        if getattr(struct, "comment", None):
            final_comments = [struct.comment]

    # Telefonlar satri
    phones_str = ", ".join(client_phones) if client_phones else "—"

    # Izoh
    comment_str = "\n".join(final_comments) if final_comments else "—"

    # --- Mahsulot matnini tozalash ---
    raw_lines = text_for_ai.splitlines()
    cleaned_product_lines = _clean_products_with_structured(
        raw_lines=raw_lines,
        phones=client_phones,
        amount=final_amount,
        client_name=client_name_parsed,
    )
    products_str = "\n".join(cleaned_product_lines) if cleaned_product_lines else "—"

    # Location
    loc = finalized.location
    if loc:
        if loc.get("type") == "telegram":
            lat = loc.get("lat")
            lon = loc.get("lon")
            loc_str = f"Telegram location\nhttps://maps.google.com/?q={lat},{lon}"
        else:
            raw_loc = loc.get("raw") or ""
            loc_type = loc.get("type", "custom")
            loc_str = f"{loc_type} location: {raw_loc}"
    else:
        loc_str = "—"

    # SUMMA (sessiyadan yoki structured AI dan)
    amount = final_amount
    if amount is not None:
        amount_str = f"{amount:,}".replace(",", " ")
        amount_line = f"💰 Summa: {amount_str} so'm"
    else:
        amount_line = "💰 Summa: —"

    # 1) Avval DB ga yozamiz va order_id olamiz
    order_id: Optional[int] = None
    try:
        # Agar save_order_row ni amount qabul qiladigan qilib kengaytirgan bo'lsangiz,
        # shu yerda amount ham yuborishingiz mumkin (masalan, amount=amount).
        order_id = save_order_row(
            settings=settings,
            message=base_message,
            phones=client_phones,
            order_text=products_str,
            location=finalized.location,
        )
    except Exception as e:
        logger.error("Failed to save order to Postgres: %s", e)

    # 2) Sarlavhaga ID qo'shamiz
    header_line = "🆕 Yangi zakaz"
    if order_id is not None:
        header_line += f" (ID: {order_id})"

    # 👤 Mijoz qatori: agar AI ism topgan bo'lsa, u ham chiqadi
    if client_name_parsed:
        client_line = (
            f"👤 Mijoz: {client_name_parsed} "
            f"(tg: {full_name}, id: {user.id})"
        )
    else:
        client_line = f"👤 Mijoz: {full_name} (id: {user.id})"

    # Yakuniy xabar matni
    msg_text = (
        f"{header_line}\n"
        f"👥 Guruhdan: {chat_title}\n"
        f"{client_line}\n\n"
        f"📞 Telefon(lar): {phones_str}\n"
        f"{amount_line}\n"
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
            "order_id": order_id,
            "chat_id": base_message.chat.id,
            "chat_title": chat_title,
            "user_id": user.id,
            "user_name": full_name,
            "phones": client_phones,
            "location": finalized.location,
            "raw_messages": finalized.raw_messages,
            "amount": amount,
            "client_name": client_name_parsed,
        },
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

    if reply_markup is not None:
        asyncio.create_task(auto_remove_cancel_keyboard(sent_msg, delay=30))

    clear_session(key)
    logger.info("Session cleared for key=%s", key)
