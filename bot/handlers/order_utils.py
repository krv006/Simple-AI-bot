# bot/handlers/order_utils.py
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

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

    "Kredit",
    "Bez kredit",
    "Hozirga naqt",
    "Srochno naqt",
    "Xozirga",

]


def normalize_digits(s: str) -> str:
    """
    Satrdan faqat raqamlarni olib qoladi.
    """
    return re.sub(r"\D", "", s or "")


def append_dataset_line(filename: str, payload: dict) -> None:
    """
    Dataset yig‘ish: har bir yozuvni alohida JSON-line sifatida faylga yozamiz.
    """
    try:
        with open(filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error("Failed to write dataset line to %s: %s", filename, e)


def choose_client_phones(raw_messages: List[str], phones: Set[str]) -> List[str]:
    """
    Xabarlar matnidan kelib chiqib qaysi telefon mijozniki, qaysi do‘konniki
    ekanini aniqlashga harakat qiladi.
    """
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

    # 1-PASS: butun xabar bo‘yicha
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


def build_final_texts(raw_messages: List[str], phones: Set[str]):
    """
    Yakuniy zakaz matni uchun:
    - mijoz telefonlari
    - product satrlar
    - comment satrlar
    ni ajratib qaytaradi.
    """
    client_phones = choose_client_phones(raw_messages, phones)
    client_digits = {
        normalize_digits(p)[-7:]
        for p in client_phones
        if normalize_digits(p)
    }

    product_lines: List[str] = []
    comment_lines: List[str] = []

    for msg in raw_messages:
        text = (msg or "").strip()
        if not text:
            continue

        low = text.lower()
        has_digits = any(ch.isdigit() for ch in text)
        digits = normalize_digits(text)

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


def make_timestamp() -> str:
    """
    UTC timestamp (ISO format) – dataset yozuvlarda ishlatish uchun.
    """
    return datetime.now(timezone.utc).isoformat()


def parse_order_message_text(text: str) -> Optional[Dict[str, Any]]:
    """
    Zakaz xabarini (🆕 Yangi zakaz ...) parslash:
    - order_id
    - chat_title
    - client_name, client_id
    - phones (list)
    - location_text (manzil satri)
    - comments (str)
    - products (str)
    """
    lines = text.splitlines()
    if not lines:
        return None

    first = lines[0]
    if not first.startswith("🆕 Yangi zakaz"):
        return None

    order_id: Optional[int] = None
    m = re.search(r"\(ID:\s*(\d+)\)", first)
    if m:
        try:
            order_id = int(m.group(1))
        except ValueError:
            order_id = None

    chat_title = ""
    client_name = ""
    client_id: Optional[int] = None
    location_text: Optional[str] = None

    for l in lines:
        if l.startswith("👥 Guruhdan:"):
            chat_title = l.split(":", 1)[1].strip()
        elif l.startswith("👤 Mijoz:"):
            # format: "👤 Mijoz: Full Name (id: 123456)"
            body = l.split("Mijoz:", 1)[1].strip() if "Mijoz:" in l else l
            if "(id:" in body:
                name_part, id_part = body.split("(id:", 1)
                client_name = name_part.strip()
                id_digits = re.findall(r"\d+", id_part)
                if id_digits:
                    try:
                        client_id = int(id_digits[0])
                    except ValueError:
                        client_id = None
            else:
                client_name = body.strip()
        elif l.startswith("📍 Manzil:"):
            location_text = l.split(":", 1)[1].strip()

    phone_line = next(
        (l for l in lines if l.startswith("📞 Telefon(lar):")), None
    )
    if phone_line:
        phones_str = phone_line.split(":", 1)[1].strip()
        phones_list = [
            p.strip()
            for p in phones_str.split(",")
            if p.strip() and p.strip() != "—"
        ]
    else:
        phones_list = []

    comment_lines = []
    products_lines = []
    state: Optional[str] = None

    for l in lines:
        if l.startswith("💬 Izoh/comment:"):
            state = "comment"
            continue
        if l.startswith("☕️ Mahsulot/zakaz matni:"):
            state = "products"
            continue

        if state == "comment":
            comment_lines.append(l)
        elif state == "products":
            products_lines.append(l)

    comments_str = "\n".join(comment_lines).strip()
    products_str = "\n".join(products_lines).strip()

    return {
        "order_id": order_id,
        "chat_title": chat_title,
        "client_name": client_name,
        "client_id": client_id,
        "phones": phones_list,
        "location_text": location_text,
        "comments": comments_str,
        "products": products_str,
    }
