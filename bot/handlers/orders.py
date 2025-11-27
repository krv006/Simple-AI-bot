# bot/handlers/orders.py
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

COMMENT_KEYWORDS = [
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


def _normalize_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _choose_client_phones(raw_messages: list[str], phones: set[str]) -> list[str]:
    """
    Bir nechta telefon bo'lsa, "mijoz" telefonini tanlashga harakat qiladi.
    Qolganlari (magazin / ofis) boshqa toifa hisoblanadi.
    """
    if not phones:
        return []

    # Xabarlarni qatorma-qator qilib olamiz
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
        phone_digits = _normalize_digits(phone)
        if not phone_digits:
            other_phones.add(phone)
            continue

        is_client = False
        is_shop = False

        for line in lines:
            line_digits = _normalize_digits(line)
            # Oxirgi 9 ta raqam bo‘yicha solishtiramiz
            tail9 = phone_digits[-9:]
            if tail9 and tail9 in line_digits:
                low = line.lower()
                if any(kw in low for kw in shop_kw):
                    is_shop = True
                if any(kw in low for kw in client_kw):
                    is_client = True

        if is_client and not is_shop:
            client_phones.add(phone)
        else:
            other_phones.add(phone)

    # Agar aniq mijoz raqamini topgan bo‘lsak – faqat shuni qaytaramiz
    if client_phones:
        return sorted(client_phones)

    # Aks holda hammasini qaytaramiz
    return sorted(phones)


def _build_final_texts(raw_messages: list[str], phones: set[str]):
    """
    Yakuniy product va comment matnlarini faqat raw_messages asosida quramiz.

    - mijozning o'zi raqami turgan qator productga kirmaydi
    - raqamli, lekin faqat telefon bo'lmagan satrlar (summa, vaqt, tavsif) productga tushadi
    - COMMENT_KEYWORDS bo'lgan raqam-siz matnlar commentga tushadi
    """
    client_phones = _choose_client_phones(raw_messages, phones)
    # mijoz raqami uchun oxirgi 9 ta raqamni olib solishtiramiz
    client_digits = {
        _normalize_digits(p)[-9:] for p in client_phones if _normalize_digits(p)
    }

    product_lines: list[str] = []
    comment_lines: list[str] = []

    for msg in raw_messages:
        text = (msg or "").strip()
        if not text:
            continue

        low = text.lower()
        has_digits = any(ch.isdigit() for ch in text)
        digits = _normalize_digits(text)

        # Agar satr faqat mijoz telefoniga teng bo'lsa -> product emas
        is_pure_client_phone = False
        if has_digits and digits:
            for cd in client_digits:
                if cd and digits.endswith(cd) and 7 <= len(digits) <= 13:
                    # odatda telefon uzunligi 9–13 raqam
                    is_pure_client_phone = True
                    break

        if has_digits and not is_pure_client_phone:
            # bu yerga summa, "412ming", "Summa 109000", kredit/oplacheno va h.k. kiradi
            product_lines.append(text)
            continue

        # raqam yo'q bo'lsa:
        if any(kw in low for kw in COMMENT_KEYWORDS):
            comment_lines.append(text)
        else:
            # Masalan, "Kichik doner + kola" – raqam bo'lmasa ham product bo'lishi mumkin
            product_lines.append(text)

    return client_phones, product_lines, comment_lines


def _has_product_candidate(raw_messages: list[str], phones: set[str]) -> bool:
    """
    Sessiyada productga o‘xshagan qatormiz bormi-yo‘qligini tekshiradi.
    Faqat mijozning yalang‘och raqami bo‘lsa, bu product hisoblanmaydi.
    """
    client_phones, products, _comments = _build_final_texts(raw_messages, phones)
    if not products:
        return False
    # xavfsizlik uchun: agar yagona product bo‘lsa va u client raqamiga aynan teng bo‘lsa – e’tiborga olmaymiz
    if len(products) == 1 and products[0] in client_phones:
        return False
    return True


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

        # Agar bu user uchun sessiya allaqachon yakunlangan bo‘lsa – keyingi gaplar ignorda
        if session.is_completed:
            logger.info("Session already completed for key=%s, skipping.", key)
            return

        # Hamma textlarni saqlaymiz – keyin shundan product/comment yig'amiz
        if text:
            session.raw_messages.append(text)

        # --- Phones ---
        had_phones_before = bool(session.phones)
        phones = extract_phones(text)
        for p in phones:
            session.phones.add(p)
        phones_new = bool(session.phones) and not had_phones_before

        # --- Location ---
        had_location_before = session.location is not None
        loc = extract_location_from_message(message)
        just_got_location = False
        if loc:
            session.location = loc
            if not had_location_before:
                just_got_location = True

        logger.info("Current session phones=%s", session.phones)
        logger.info("Current session location=%s", session.location)

        # --- AI classification faqat “zakazga aloqador/emas” va triggering uchun ---
        ai_result = await classify_text_ai(settings, text, session.raw_messages)
        role = ai_result.get("role", "UNKNOWN")
        has_addr_kw = ai_result.get("has_address_keywords", False)
        is_order_related = ai_result.get("is_order_related", False)

        logger.info("AI result=%s", ai_result)

        low = text.lower()
        has_digits = any(ch.isdigit() for ch in text)

        # Qo‘shimcha rule-based: summa / min / ming kabi so‘zlar
        if role == "UNKNOWN":
            money_kw = ["summa", "suma", "sum", "ming", "min", "мин", "минг", "сум", "сом", "тыс"]
            if has_digits or any(kw in low for kw in money_kw):
                role = "PRODUCT"
            if any(kw in low for kw in COMMENT_KEYWORDS):
                role = "COMMENT"

        # Bu paytgacha yig‘ilgan xabarlar ichida zakazga o‘xshagan qatormiz bormi?
        has_product_candidate = _has_product_candidate(session.raw_messages, session.phones)

        # Zakazga aloqador bo‘lmagan, telefon/loc yo‘q oddiy gaplarni error guruhga o‘tkazamiz
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

        session.updated_at = datetime.now(timezone.utc)

        ready = is_session_ready(session)
        logger.info(
            "Session ready=%s | is_completed=%s | just_got_location=%s | phones_new=%s | has_product_candidate=%s",
            ready,
            session.is_completed,
            just_got_location,
            phones_new,
            has_product_candidate,
        )

        if not ready or session.is_completed:
            return

        # Finalize shartlari:
        # 1) Lokatsiya endi keldi VA allaqachon productga o‘xshagan textlar bor
        # 2) Yoki hozirgi xabar PRODUCT rolda bo‘lsa (summa / zakaz matni) va sessiya tayyor bo‘lsa
        # 3) Yoki adres kalit so‘zlari bor bo‘lsa (has_addr_kw) va sessiya tayyor bo‘lsa
        # 4) Yoki telefon endi keldi (phones_new) VA oldin product candidate bo‘lsa (masalan, avval summa + loc edi)
        should_finalize = (
                (just_got_location and has_product_candidate)
                or (role == "PRODUCT" and ready)
                or (has_addr_kw and ready)
                or (phones_new and has_product_candidate and ready)
        )

        if not should_finalize:
            logger.info("Session is ready, but current message is not a finalize trigger.")
            return

        finalized = finalize_session(key)
        logger.info("Finalizing session key=%s, finalized=%s", key, bool(finalized))
        if not finalized:
            return

        # Yakuniy product/commentlarni faqat raw_messages asosida qayta hisoblaymiz
        client_phones, final_products, final_comments = _build_final_texts(
            finalized.raw_messages, finalized.phones
        )

        # JSON uchun ham shu yangilangan qiymatlarni berib qo‘yamiz
        try:
            finalized.product_texts = final_products
            finalized.comments = final_comments
        except Exception:
            # Agar dataclassda bu fieldlar bo‘lmasa ham bot yiqilmasin
            pass

        chat_title = message.chat.title or "Noma'lum guruh"
        user = message.from_user
        full_name = user.full_name if user.full_name else f"id={user.id}"

        phones_str = ", ".join(client_phones) if client_phones else "—"
        comment_str = "\n".join(final_comments) if final_comments else "—"
        products_str = "\n".join(final_products) if final_products else "—"

        loc = finalized.location
        if loc:
            if loc["type"] == "telegram":
                lat = loc["lat"]
                lon = loc["lon"]
                loc_str = f"Telegram location\nhttps://maps.google.com/?q={lat},{lon}"
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
