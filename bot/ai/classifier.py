# bot/ai/classifier.py
import json
import re
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..db import get_active_prompt_config


def _simple_rule_based(text: str) -> Dict[str, Any]:
    """
    Oddiy rule-based klassifikator (backup variant).
    Faqat klassifikatsiya qiladi, extraction yo'q.
    """
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
        # prompt-config asosidagi extraction bo'lmagan holat, shuning uchun None
        "extraction": None,
    }


def _build_system_prompt_from_config(config: Dict[str, Any]) -> str:
    """
    DB'dan olingan prompt_config (payload) ni system prompt stringga aylantiradi.
    Bu siz avval bergan katta JSON (phones, amount, address, comment...) uchun mo'ljallangan.
    """
    meta = config.get("meta", {})
    rules = config.get("rules", {})
    output_schema = config.get("output_schema", {})
    examples = config.get("examples", [])

    lines: List[str] = []

    desc = meta.get("description") or "Telegram zakaz bot uchun AI klassifikator"
    lines.append(desc)
    lines.append("Quyidagi qoidalarga qat'iy amal qil:")

    # Rules bo'limlari
    for section_name, section_rules in rules.items():
        lines.append(f"\n[{section_name}] qoidalari:")
        for r in section_rules:
            lines.append(f"- {r}")

    # Output schema
    lines.append("\nFaqat JSON obyekt qaytar, hech qanday matnli izoh yozma.")
    lines.append("JSON struktura taxminan quyidagicha bo'lishi kerak:")
    lines.append(json.dumps(output_schema, ensure_ascii=False, indent=2))

    # Examples – few-shot sifatida
    if examples:
        lines.append("\nQuyida kirish va kutilgan chiqish misollari:")
        for idx, ex in enumerate(examples[:5], start=1):
            lines.append(f"\nMisol #{idx}")
            lines.append("Input:")
            lines.append(ex.get("input", ""))
            lines.append("Kutilgan JSON:")
            lines.append(json.dumps(ex.get("expected_output", {}), ensure_ascii=False, indent=2))

    return "\n".join(lines)


def _derive_classification_from_extraction(
        text: str,
        extraction: Dict[str, Any],
) -> Dict[str, Any]:
    tl = text.lower()

    phones = extraction.get("phones") or []
    amount = extraction.get("amount")
    address = extraction.get("address") or {}
    addr_type = address.get("type") if isinstance(address, dict) else None

    has_addr = addr_type in ("text", "location_url")
    has_amount = amount is not None
    has_phones = bool(phones)

    # Default qiymatlar
    is_order_related = False
    role = "UNKNOWN"
    reason = ""
    order_probability = 0.2

    greeting_keywords = [
        "salom",
        "assalomu",
        "qalesiz",
        "привет",
        "hello",
        "hi",
        "добрый день",
    ]
    is_greeting = any(k in tl for k in greeting_keywords)

    if not has_phones and not has_amount and not has_addr:
        if is_greeting:
            is_order_related = False
            role = "RANDOM"
            reason = "Extraction natijasida telefon/summa/manzil aniqlanmadi, xabar salomlashishga o‘xshaydi."
            order_probability = 0.05
        else:
            is_order_related = False
            role = "UNKNOWN"
            reason = "Extraction natijasida zakaz uchun kerakli maydonlar topilmadi."
            order_probability = 0.2

        return {
            "is_order_related": is_order_related,
            "role": role,
            "has_address_keywords": has_addr,
            "reason": reason,
            "order_probability": float(order_probability),
        }

    # Agar telefon yoki summa bo'lsa – bu deyarli aniq zakaz matni
    if has_phones or has_amount:
        is_order_related = True
        role = "PRODUCT"
        fragments = []
        if has_phones:
            fragments.append("telefon raqam(lar) topildi")
        if has_amount:
            fragments.append("summa topildi")
        if has_addr:
            fragments.append("manzil ham aniqlangan")

        reason = " , ".join(fragments) + ". Extraction natijasiga ko‘ra bu zakaz matni (PRODUCT)."
        order_probability = 0.9

    # Faqat manzil bo'lsa – COMMENT
    elif has_addr and not has_phones and not has_amount:
        is_order_related = True
        role = "COMMENT"
        reason = "Extraction natijasida faqat manzil (address) aniqlangan, bu zakazga tegishli izoh/manzil bo‘lagi."
        order_probability = 0.7

    return {
        "is_order_related": is_order_related,
        "role": role,
        "has_address_keywords": has_addr,
        "reason": reason,
        "order_probability": float(order_probability),
    }


async def classify_text_ai(
        settings: Settings,
        text: str,
        context_messages: List[str],
) -> Dict[str, Any]:
    """
    Asosiy AI klassifikator:
    - agar OpenAI o'chirilgan bo'lsa → faqat rule-based
    - agar OpenAI yoqilgan bo'lsa:
        * DB'dan active prompt_config oladi (phones/amount/address/comment prompt)
        * shu prompt bilan extraction qiladi
        * extraction natijasidan klassifikatsiya field'larini hisoblaydi
    Natija:
    {
      "is_order_related": bool,
      "role": "PRODUCT" | "COMMENT" | "RANDOM" | "UNKNOWN",
      "has_address_keywords": bool,
      "reason": str,
      "order_probability": float,
      "source": "RULES" | "OPENAI_PROMPT_CONFIG" | "OPENAI_CLASSIC",
      "extraction": dict | None   # phones/amount/address/comment/... extraction
    }
    """
    if not text.strip():
        return {
            "is_order_related": False,
            "role": "UNKNOWN",
            "has_address_keywords": False,
            "reason": "Bo'sh yoki faqat bo'sh joylardan iborat xabar.",
            "order_probability": 0.0,
            "source": "RULES",
            "extraction": None,
        }

    # OpenAI o'chirilgan bo'lsa – faqat rule-based
    if not settings.openai_enabled:
        return _simple_rule_based(text)

    # OpenAI yoqilgan – harakat qilib ko'ramiz
    try:
        from openai import OpenAI
    except Exception as e:
        print("OpenAI kutubxonasini import qilishda xato, rule-basedga qaytyapman:", repr(e))
        return _simple_rule_based(text)

    try:
        client = OpenAI(api_key=settings.openai_api_key)

        # DB'dan active prompt_config ni olib ko'ramiz
        prompt_config: Optional[Dict[str, Any]] = None
        try:
            prompt_config = get_active_prompt_config(settings)
        except Exception as e:
            print("get_active_prompt_config xato:", repr(e))
            prompt_config = None

        if prompt_config:
            # 1) prompt_config asosida extraction qilish
            system_prompt = _build_system_prompt_from_config(prompt_config)

            # Kontekst xabarlarni ham qo'shsak bo'ladi (ixtiyoriy)
            user_prompt = (
                    "Quyidagi xabarni tahlil qilib, promptdagi qoidalarga muvofiq "
                    "telefon raqamlar, summa, manzil va izohlarni JSON ko'rinishida qaytar.\n\n"
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

            result_text = resp.choices[0].message.content or ""
            extraction = json.loads(result_text)

            # Extraction natijasidan klassifikatsiya hosil qilamiz
            cls = _derive_classification_from_extraction(text, extraction)

            return {
                "is_order_related": bool(cls.get("is_order_related", False)),
                "role": cls.get("role", "UNKNOWN"),
                "has_address_keywords": bool(cls.get("has_address_keywords", False)),
                "reason": cls.get("reason", ""),
                "order_probability": float(cls.get("order_probability", 0.5)),
                "source": "OPENAI_PROMPT_CONFIG",
                "extraction": extraction,
            }

        # prompt_config yo'q bo'lsa – eski klassifikatsiya prompti bilan ishlaymiz
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

        result_text = resp.choices[0].message.content or ""
        data = json.loads(result_text)

        return {
            "is_order_related": bool(data.get("is_order_related", False)),
            "role": data.get("role", "UNKNOWN"),
            "has_address_keywords": bool(data.get("has_address_keywords", False)),
            "reason": data.get("reason", ""),
            "order_probability": float(data.get("order_probability", 0.5)),
            "source": "OPENAI_CLASSIC",
            "extraction": None,
        }

    except Exception as e:
        print("OpenAI xato, rule-basedga qaytyapman:", repr(e))
        return _simple_rule_based(text)
