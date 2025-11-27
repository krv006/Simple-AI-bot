import logging
import re
from datetime import datetime, timezone

from aiogram import Dispatcher, F
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import Message

from ..ai.classifier import classify_text_ai
from ..config import Settings
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


def _split_product_and_comment_texts(product_texts: list[str], comments: list[str]):
    """
    Kuryer/joylashuvga oid gaplarni product emas, comment qilib yuborish uchun
    kichik heuristika.
    Masalan: "Baliqchiga kuryer kk", "eshik oldida kutib turadi" va hokazo.
    """
    comment_keywords = [
        "kuryer",
        "kurier",
        "kur'er",
        "курьер",
        "eshik oldida",
        "uyga olib chiqib bering",
        "moshinada kuting",
        "машинада кутиб",
        "baliqchiga",
        "baliqchi",
        "klientga",
        "к клиенту",
    ]

    new_products: list[str] = []
    new_comments = list(comments)

    for text in product_texts:
        low = text.lower()

        has_digits = bool(re.search(r"\d", text))
        if any(kw in low for kw in comment_keywords) and not has_digits:
            new_comments.append(text)
        else:
            new_products.append(text)

    return new_products, new_comments


def _choose_client_phones(raw_messages: list[str], phones: set[str]) -> list[str]:
    """
    Matn ichida bir nechta telefon bo'lsa, faqat "mijoz" telefonini ajratishga harakat qiladi.
    Heuristika:

    - Agar telefon turgan qatorda "клиент", "mijoz" va shunga o'xshash so'zlar bo'lsa -> client phone.
    - Agar qatorda "нашего магазина", "магазин", "our shop" bo'lsa -> bu client emas.
    - Agar client_phones topilmasa, fallback qilib barcha telefonlarni qaytaramiz.
    """
    if not phones:
        return []

    lines: list[str] = []
    for msg in raw_messages:
        for line in msg.splitlines():
            line = line.strip()
            if line:
                lines.append(line)

    client_kw = [
        "номер клиента",
        "клиента",
        "клиент",
        "mijoz",
        "mijoz tel",
        "telefon klienta",
        "номер клиентa",
    ]
    shop_kw = [
        "номер нашего магазина",
        "нашего магазина",
        "магазин",
        "magazin",
        "our shop",
        "номер магазина",
    ]

    client_phones: set[str] = set()
    other_phones: set[str] = set()

    for phone in phones:

        phone_digits = re.sub(r"\D", "", phone)
        if not phone_digits:
            other_phones.add(phone)
            continue

        is_client = False
        is_shop = False

        for line in lines:
            line_digits = re.sub(r"\D", "", line)
            if phone_digits[-7:] in line_digits:
                low = line.lower()
                if any(kw in low for kw in shop_kw):
                    is_shop = True
                if any(kw in low for kw in client_kw):
                    is_client = True

        if is_client and not is_shop:
            client_phones.add(phone)
        else:
            other_phones.add(phone)

    if client_phones:
        return sorted(client_phones)
    return sorted(phones)


def register_order_handlers(dp: Dispatcher, settings: Settings) -> None:
    @dp.message(CommandStart())
    async def cmd_start(message: Message):
        await message.answer(
            "Assalomu alaykum!\n"
            "Men AI asosida zakaz xabarlarini yig'ib beradigan botman.\n"
            "Meni guruhga qo'shing va mijoz xabarlarini yuboring."
        )

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

        phones = extract_phones(text)
        for p in phones:
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

        ai_result = await classify_text_ai(settings, text, session.raw_messages)
        role = ai_result.get("role", "UNKNOWN")
        has_addr_kw = ai_result.get("has_address_keywords", False)
        is_order_related = ai_result.get("is_order_related", False)

        logger.info("AI result=%s", ai_result)

        if (
                settings.error_group_id
                and not is_order_related
                and not phones
                and not message.location
                and text.strip()
        ):
            src_chat_title = message.chat.title or str(message.chat.id)
            user = message.from_user
            full_name = user.full_name if user and user.full_name else f"id={user.id}"

            error_text = (
                f"👥 Guruh: {src_chat_title}\n"
                f"👤 User: {full_name} (id: {user.id})\n\n"
                f"📩 Xabar:\n{text}"
            )

            try:
                await message.bot.send_message(settings.error_group_id, error_text)
            except TelegramBadRequest as e:
                logger.error(
                    "Failed to send non-order message to error_group_id=%s: %s",
                    settings.error_group_id,
                    e,
                )

            return

        if role == "PRODUCT":
            if text:
                session.product_texts.append(text)
        elif role == "COMMENT" or has_addr_kw:
            if text:
                session.comments.append(text)

        session.updated_at = datetime.now(timezone.utc)

        ready = is_session_ready(session)

        logger.info(
            "Session ready=%s | is_completed=%s | just_got_location=%s | phones_new=%s",
            ready,
            session.is_completed,
            just_got_location,
            phones_new,
        )

        if not ready or session.is_completed:
            return

        should_finalize = (
                just_got_location
                or role == "PRODUCT"
                or has_addr_kw
                or phones_new
        )

        if not should_finalize:
            logger.info("Session is ready, but current message is not a finalize trigger.")
            return

        finalized = finalize_session(key)
        logger.info("Finalizing session key=%s, finalized=%s", key, bool(finalized))
        if not finalized:
            return

        cleaned_products, cleaned_comments = _split_product_and_comment_texts(
            finalized.product_texts, finalized.comments
        )

        client_phones = _choose_client_phones(finalized.raw_messages, finalized.phones)

        chat_title = message.chat.title or "Noma'lum guruh"
        user = message.from_user
        full_name = user.full_name if user.full_name else f"id={user.id}"

        phones_str = ", ".join(client_phones) if client_phones else "—"
        comment_str = "\n".join(cleaned_comments) if cleaned_comments else "—"
        products_str = "\n".join(cleaned_products) if cleaned_products else "—"

        loc = finalized.location
        if loc:
            if loc["type"] == "telegram":
                lat = loc["lat"]
                lon = loc["lon"]
                loc_str = f"Telegram location\nhttps://maps.google.com/?q={lat},{lon}"
            else:
                raw = loc["raw"] or ""
                loc_str = f"{loc['type']} location: {raw}"
        else:
            loc_str = "—"

        msg_text = (
            f"🆕 Yangi zakaz\n"
            f"👥 Guruhdan: {chat_title}\n"
            f"👤 Mijoz: {full_name} (id: {user.id})\n\n"
            f"📞 Telefon(lar): {phones_str}\n"
            f"📍 Manzil: {loc_str}\n"
            f"💬 Izoh/comment:\n{comment_str}\n\n"
            f"☕ Mahsulot/zakaz matni:\n{products_str}"
        )

        save_order_to_json(finalized)
        logger.info("Order saved to ai_bot.json for key=%s", key)

        target_chat_id = settings.send_group_id or message.chat.id
        logger.info("Sending order to target group=%s", target_chat_id)

        try:
            await message.bot.send_message(target_chat_id, msg_text)
        except TelegramBadRequest as e:
            logger.error(
                "Failed to send order to target_chat_id=%s: %s. "
                "Falling back to source chat_id=%s",
                target_chat_id,
                e,
                message.chat.id,
            )
            await message.answer(msg_text)

        clear_session(key)
        logger.info("Session cleared for key=%s", key)
