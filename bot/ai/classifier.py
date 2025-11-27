# bot/ai/classifier.py
import json
import re
from typing import Any, Dict, List

from ..config import Settings


def _simple_rule_based(text: str) -> Dict[str, Any]:
    tl = text.lower()

    # Manzil so'zlari
    address_keywords = [
        "dom", "kv", "kv.", "kvartira", "подъезд", "подьезд",
        "uy", "eshik", " подъезд", "kvartir", "подъез", "подьез",
        "дом", "улица", "улиц", "mavze", "orqa eshik", "oldi", "oldida",
        "mahalla", "mahallasi", "rayon", "tuman", "район", "квартал"
    ]

    # Produkt / ovqat / ichimlik so'zlari
    product_keywords = [
        "latte", "капучино", "cappuccino", "americano", "kofe", "coffee",
        "espresso", "эспрессо",
        "pizza", "burger", "lavash", "doner", "donar", "donerchi",
        "set", "combo", "kombo"
    ]

    # Narx, summa, vaqt, kredit haqida so'zlar
    amount_keywords = [
        "summa", "sum", "summasi",
        "ming", "min", "мин", "minut", "минут",
        "oplacheno", "oplata", "oplachen", "оплачено",
        "kredit", "bezkredit", "bez kredit", "кредит",
        "tolov", "tolovsz", "to'lov", "tolanadi",
        "oplata nal", "nal"
    ]

    has_addr = any(k in tl for k in address_keywords)
    has_prod = any(k in tl for k in product_keywords)
    has_amount_kw = any(k in tl for k in amount_keywords)

    # Raqam + "ming/min" patternlari: "412ming", "412 ming", "277 000" va hokazo
    amount_pattern = (
        re.search(r"\b\d{2,4}\s*(ming|min|мин|minut|минут)\b", tl)
        or re.search(r"\b\d{2,3}\s*000\b", tl)  # 277 000; 234 000 va hokazo
        or re.search(r"\bsumma\s*\d+", tl)      # "summa 412" kabi
    )
    has_amount_pattern = bool(amount_pattern)

    has_amount = has_amount_kw or has_amount_pattern

    # --- Klassifikatsiya logikasi ---
    # 1) Agar ovqat / summa / vaqt haqida bo'lsa -> PRODUCT
    if (has_prod or has_amount) and not has_addr:
        role = "PRODUCT"
        is_order_related = True

    # 2) Agar manzil so'zlari bo'lsa -> COMMENT (izoh / manzil)
    elif has_addr:
        role = "COMMENT"
        is_order_related = True

    else:
        # Oddiy salomlashish va hokazo -> RANDOM
        greeting_keywords = ["salom", "assalomu", "qalesiz", "как дела", "привет", "hello", "hi"]
        if any(k in tl for k in greeting_keywords):
            role = "RANDOM"
            is_order_related = False
        else:
            role = "UNKNOWN"
            is_order_related = False

    return {
        "is_order_related": is_order_related,
        "role": role,
        "has_address_keywords": has_addr,
    }


async def classify_text_ai(
    settings: Settings,
    text: str,
    context_messages: List[str],
) -> Dict[str, Any]:
    """
    Xabarni AI orqali klassifikatsiya qilish.
    AI ishlamasa, _simple_rule_based ga qaytadi.
    """
    if not text.strip():
        return {
            "is_order_related": False,
            "role": "UNKNOWN",
            "has_address_keywords": False,
        }

    # Agar OpenAI o'chirilgan bo'lsa -> faqat rule-based
    if not getattr(settings, "openai_enabled", False):
        return _simple_rule_based(text)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)

        system_prompt = (
            "Siz Telegram guruhidagi xabarlarni klassifikatsiya qiladigan yordamchisiz.\n"
            "Maqsad: xabar zakazga aloqador yoki yo'qligini aniqlash.\n\n"
            "Faqat quyidagi JSON formatda javob qaytaring:\n"
            "{\n"
            '  \"is_order_related\": bool,\n'
            '  \"role\": \"PRODUCT\" | \"COMMENT\" | \"RANDOM\" | \"UNKNOWN\",\n'
            '  \"has_address_keywords\": bool\n'
            "}\n\n"
            "Ta'riflar:\n"
            "- \"PRODUCT\": zakaz mazmuni, summa, narx, vaqt, kredit/oplata haqida ma'lumotlar.\n"
            "  Masalan:\n"
            "    \"277 000\", \"234 ming\", \"412ming\", \"412 min\",\n"
            "    \"Summa 412ming\", \"kredit\", \"bezkredit\", \"oplacheno\",\n"
            "    \"latte 2ta\", \"pizza 1 dona\" va hokazo.\n"
            "- \"COMMENT\": manzil, qanday olib chiqish, eshik/kvartira/podyezd,\n"
            "  \"Chilonzor 5 mavze 14 uy 43 xona\", \"eshik oldida kutib turaman\" kabi manzil/izoh.\n"
            "- \"RANDOM\": zakazga aloqasi yo'q gaplar (salomlashish, chat, hazil va hokazo).\n"
            "- \"UNKNOWN\": aniqlab bo'lmaydigan xabarlar.\n\n"
            "Agar xabarda summa, narx yoki vaqt ko'rsatilgan bo'lsa:\n"
            "- \"412ming\", \"412 ming\", \"277 000\", \"20 minut\", \"10 min\", \"Summa 234 ming\" kabi,\n"
            "  ularni albatta zakazga tegishli PRODUCT deb hisoblang.\n"
        )

        user_prompt = (
            "Kontekst xabarlar (oxirgi 5 ta):\n"
            + "\n".join(f"- {m}" for m in context_messages[-5:])
            + "\n\nTahlil qilinadigan xabar:\n"
            + text
        )

        resp = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )

        result_text = resp.choices[0].message.content
        data = json.loads(result_text)

        return {
            "is_order_related": bool(data.get("is_order_related", False)),
            "role": data.get("role", "UNKNOWN"),
            "has_address_keywords": bool(data.get("has_address_keywords", False)),
        }
    except Exception as e:
        print("OpenAI xato, rule-basedga qaytyapman:", repr(e))
        return _simple_rule_based(text)
