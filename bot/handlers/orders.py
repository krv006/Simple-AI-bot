# bot/handlers/order.py
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import List

from aiogram import Dispatcher, F
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types import Message, CallbackQuery

from ..ai.classifier import classify_text_ai
from ..config import Settings
from ..db import save_order_row, cancel_order_row
from ..storage import (
    get_or_create_session,
    get_session_key,
    is_session_ready,
    finalize_session,
    clear_session,
    save_order_to_json,
)
from ..utils.locations import extract_location_from_message
from ..utils.phones import extract_phones

logger = logging.getLogger(__name__)

COMMENT_KEYWORDS = [
    "kuryer",
    "kurier",
    "kur'er",
    "курьер",

    "eshik oldida",
    "eshik oldida kut",
    "eshik oldida kutib",
    "eshik oldida kutib turaman",
    "eshik oldida kutib turing",

    "uyga olib chiqib bering",
    "uyga olib chiqib ber",
    "uyga olib chiqing",
    "uyga obchiqib bering",

    "orqa eshik",
    "oldi eshik",
    "oldida kutaman",
    "kutib turaman",
    "moshinada kuting",
    "машинада кутиб",

    "к клиенту",
    "klientga",

    "подъезд",
    "подьезд",
    "подъез",
    "подьез",
    "podezd",
    "podyezd",

    "этаж",
    "etaж",
    "etaj",
    "qavat",

    "kvartira",
    "kv.",
    "kv ",
    "квартир",
    "кв ",

    "dom",
    "дом",
    "uy",
    "mahalla",
    "mahallasi",
    "mavze",
    "район",
    "tuman",
]


def _normalize_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _append_dataset_line(filename: str, payload: dict) -> None:
    try:
        with open(filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("Failed to write dataset line to %s: %s", filename, e)


def _choose_client_phones(raw_messages: List[str], phones: set[str]) -> List[str]:
    if not phones:
        return []

    phones = set(phones)

    client_kw = [
        "номер клиента",
        "клиента",
        "клиент:",
        "клиент ",
        "mijoz",
        "mijoz:",
        "mijoz tel",
        "telefon klienta",
        "номер клиентa",
        "покупатель",
        "номер покупателя",
        "client",
        "klient",
    ]

    shop_kw = [
        "номер нашего магазина",
        "нашего магазина",
        "наш магазин",
        "магазин",
        "magazin",
        "our shop",
        "номер магазина",
        "kids plate",
        "kidsplate",
        "магазин детского питания",
        "наша точка",
        "наш номер",
        "наш тел",
        "наш телефон",
    ]

    phone_role: dict[str, str] = {p: "unknown" for p in phones}

    # 1-PASS: butun xabar bo‘yicha kontekst
    for msg in raw_messages:
        low_msg = (msg or "").lower()
        msg_phones = extract_phones(msg)
        if not msg_phones:
            continue

        msg_is_shop = any(kw in low_msg for kw in shop_kw)
        msg_is_client = any(kw in low_msg for kw in client_kw)

        for p in msg_phones:
            if p not in phone_role:
                phone_role[p] = "unknown"

            if msg_is_shop:
                phone_role[p] = "shop"
            elif msg_is_client and phone_role.get(p) != "shop":
                phone_role[p] = "client"

    # 2-PASS: satr darajasida aniqlik kiritish
    for msg in raw_messages:
        for line in (msg or "").splitlines():
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            line_phones = extract_phones(line)
            if not line_phones:
                continue

            is_shop_line = any(kw in low for kw in shop_kw)
            is_client_line = any(kw in low for kw in client_kw)

            for p in line_phones:
                if p not in phone_role:
                    phone_role[p] = "unknown"

                if is_shop_line:
                    phone_role[p] = "shop"
                elif is_client_line and phone_role.get(p) != "shop":
                    phone_role[p] = "client"

    client_phones = [p for p, role in phone_role.items() if role == "client"]
    if client_phones:
        return sorted(set(client_phones))

    non_shop_phones = [p for p, role in phone_role.items() if role != "shop"]
    non_shop_phones = sorted(set(non_shop_phones))

    if len(non_shop_phones) == 1:
        return non_shop_phones

    return non_shop_phones or sorted(phones)


def _build_final_texts(raw_messages: List[str], phones: set[str]):
    client_phones = _choose_client_phones(raw_messages, phones)
    client_digits = {
        _normalize_digits(p)[-7:]
        for p in client_phones
        if _normalize_digits(p)
    }

    product_lines: List[str] = []
    comment_lines: List[str] = []

    for msg in raw_messages:
        text = (msg or "").strip()
        if not text:
            continue

        low = text.lower()
        has_digits = any(ch.isdigit() for ch in text)
        digits = _normalize_digits(text)

        # Telefon satrlarini tashlab yuboramiz
        if extract_phones(text):
            if any(
                    kw in low
                    for kw in [
                        "номер телефона",
                        "номер клиента",
                        "телефон:",
                        "telefon:",
                        "телефон ",
                        "telefon ",
                    ]
            ):
                continue

        # Avval izoh kalit so‘zlari
        if any(kw in low for kw in COMMENT_KEYWORDS):
            comment_lines.append(text)
            continue

        # Faqat client telefoni bo'lgan satrni productga qo‘shmaymiz
        is_pure_client_phone = False
        if has_digits and digits:
            for cd in client_digits:
                if cd and digits.endswith(cd) and len(digits) <= 13:
                    is_pure_client_phone = True
                    break

        if has_digits and not is_pure_client_phone:
            product_lines.append(text)
            continue

        product_lines.append(text)

    return client_phones, product_lines, comment_lines


def register_order_handlers(dp: Dispatcher, settings: Settings) -> None:
    async def _auto_remove_cancel_keyboard(order_message: Message, delay: int = 10):
        """
        10 sekunddan keyin inline keyboardni avtomatik olib tashlash.
        DB statusiga tegmaydi, faqat tugmani o'chiradi.
        """
        await asyncio.sleep(delay)
        try:
            await order_message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest as e:
            logger.warning("Failed to auto-remove inline keyboard: %s", e)

    async def _finalize_and_send_after_delay(
            key: str,
            base_message: Message,
    ):
        """
        Session tayyor bo'lgach ham darhol emas, 5 sekunddan keyin finalize + send qilish.
        Shu orada kelgan qo'shimcha izoh xabarlar ham session.raw_messages ichiga tushadi.
        """
        await asyncio.sleep(5)

        finalized = finalize_session(key)
        logger.info("Delayed finalize for key=%s, finalized=%s", key, bool(finalized))
        if not finalized:
            return

        client_phones, final_products, final_comments = _build_final_texts(
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
                loc_str = (
                    f"Telegram location\nhttps://maps.google.com/?q={lat},{lon}"
                )
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

        # Dataset fayl
        _append_dataset_line(
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

        # 1) DB ga yozamiz (status = TRUE). save_order_row dan order_id qaytishi kerak.
        order_id = None
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

        # 4) 10 sekunddan keyin inline keyboardni avtomatik olib tashlash
        if reply_markup is not None:
            asyncio.create_task(_auto_remove_cancel_keyboard(sent_msg, delay=10))

        clear_session(key)
        logger.info("Session cleared for key=%s", key)

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

            _append_dataset_line(
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

            _append_dataset_line(
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
            _finalize_and_send_after_delay(
                key=key,
                base_message=message,
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
