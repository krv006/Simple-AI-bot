# bot/ai/classifier.py
import json
import re
from typing import Any, Dict, List

from ..config import Settings


def _simple_rule_based(text: str) -> Dict[str, Any]:
    tl = text.lower()

    address_keywords = [
        "dom", "kv", "kv.", "kvartira",
        "подъезд", "подьезд", "подъез", "подьез",
        "podezd", "podyezd",
        "uy", "eshik", " подъезд",
        "kvartir", "подъезда", "подъезде",
        "дом", "улица", "улиц",
        "mavze", "mavzesi",
        "orqa eshik", "oldi eshik",
        "oldida", "oldida kutaman",
        "mahalla", "mahallasi",
        "rayon", "tuman", "район", "квартал",
        "etaj", "этаж", "qavat",
    ]

    product_keywords = [
        "latte", "капучино", "cappuccino", "americano", "kofe", "coffee",
        "espresso", "эспрессо",
        "pizza", "burger", "lavash", "doner", "donar", "donerchi",
        "set", "combo", "kombo",
    ]

    amount_keywords = [
        "summa", "sum", "summasi",
        "ming", "min", "мин", "minut", "минут",
        "oplacheno", "oplata", "oplachen", "оплачено",
        "kredit", "bezkredit", "bez kredit", "кредит",
        "tolov", "tolovsz", "to'lov", "tolanadi",
        "oplata nal", "nal",
    ]

    has_addr = any(k in tl for k in address_keywords)
    has_prod = any(k in tl for k in product_keywords)
    has_amount_kw = any(k in tl for k in amount_keywords)

    amount_pattern = (
            re.search(r"\b\d{2,4}\s*(ming|min|мин|minut|минут)\b", tl)
            or re.search(r"\b\d{2,3}\s*000\b", tl)
            or re.search(r"\bsumma\s*\d+", tl)
    )
    has_amount_pattern = bool(amount_pattern)

    has_amount = has_amount_kw or has_amount_pattern

    reason = ""
    order_probability = 0.1  # default

    if (has_prod or has_amount) and not has_addr:
        role = "PRODUCT"
        is_order_related = True
        reason = (
            "Matnda mahsulot/summa/oplata bo‘yicha so‘zlar bor, "
            "manzil kalit so‘zlari yo‘q. Bu zakaz mazmuni deb baholanmoqda."
        )
        order_probability = 0.85

    elif has_addr:
        role = "COMMENT"
        is_order_related = True
        reason = (
            "Matnda manzilga oid kalit so‘zlar aniqlangan "
            "(uy, dom, mahalla, rayon va hokazo). "
            "Bu zakazga tegishli izoh/manzil bo‘lagi."
        )
        order_probability = 0.7

    else:
        greeting_keywords = [
            "salom",
            "assalomu",
            "qalesiz",
            "как дела",
            "привет",
            "hello",
            "hi",
        ]
        if any(k in tl for k in greeting_keywords):
            role = "RANDOM"
            is_order_related = False
            reason = "Xabar salomlashish / umumiy chat mazmunida, zakazga aloqasi yo‘q."
            order_probability = 0.05
        else:
            role = "UNKNOWN"
            is_order_related = False
            reason = (
                "Xabardan mahsulot, summa yoki manzil bo‘yicha aniq signallar topilmadi, "
                "shuning uchun UNKNOWN deb baholandi."
            )
            order_probability = 0.2

    return {
        "is_order_related": is_order_related,
        "role": role,
        "has_address_keywords": has_addr,
        "reason": reason,
        "order_probability": float(order_probability),
        "source": "RULES",
    }


async def classify_text_ai(
        settings: Settings,
        text: str,
        context_messages: List[str],
) -> Dict[str, Any]:
    if not text.strip():
        return {
            "is_order_related": False,
            "role": "UNKNOWN",
            "has_address_keywords": False,
            "reason": "Bo'sh yoki faqat bo'sh joylardan iborat xabar.",
            "order_probability": 0.0,
            "source": "RULES",
        }

    # OpenAI o'chirilgan bo'lsa – faqat rule-based
    if not settings.openai_enabled:
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
            '  \"has_address_keywords\": bool,\n'
            '  \"reason\": string,\n'
            '  \"order_probability\": number\n'
            "}\n\n"
            "Ta'riflar:\n"
            "- \"PRODUCT\": zakaz mazmuni, summa, narx, vaqt, kredit/oplata haqida ma'lumotlar.\n"
            "  Masalan:\n"
            "    \"277 000\", \"234 ming\", \"412ming\", \"412 min\",\n"
            "    \"Summa 412ming\", \"kredit\", \"bezkredit\", \"oplacheno\",\n"
            "    \"latte 2ta\", \"pizza 1 dona\" va hokazo.\n"
            "- \"COMMENT\": manzil, qanday olib chiqish, eshik/kvartira/podyezd,\n"
            "  \"Chilonzor 5 mavze 14 uy 43 xona\", "
            "\"eshik oldida kutib turaman\" kabi manzil/izoh.\n"
            "- \"RANDOM\": zakazga aloqasi yo'q gaplar (salomlashish, chat, hazil va hokazo).\n"
            "- \"UNKNOWN\": aniqlab bo'lmaydigan xabarlar.\n\n"
            "Agar xabarda summa, narx yoki vaqt ko'rsatilgan bo'lsa:\n"
            "- \"412ming\", \"412 ming\", \"277 000\", \"20 minut\", \"10 min\", "
            "\"Summa 234 ming\" kabi,\n"
            "  ularni albatta zakazga tegishli PRODUCT deb hisoblang.\n"
            "Har doim 'reason' maydonida qat'iy va aniq tushuntirish yozing: "
            "nega shu rolni tanladingiz.\n"
            "'order_probability' 0 dan 1 gacha real son bo‘lsin.\n"
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
            "reason": data.get("reason", ""),
            "order_probability": float(data.get("order_probability", 0.5)),
            "source": "OPENAI",
        }
    except Exception as e:
        print("OpenAI xato, rule-basedga qaytyapman:", repr(e))
        return _simple_rule_based(text)
