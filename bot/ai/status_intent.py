# bot/ai/status_intent.py
import json
from typing import List

from ..config import Settings


def _simple_status_rule_based(text: str) -> bool:
    """
    OpenAI o'chirilgan yoki xato bo'lgan holatda ishlaydigan oddiy rule-based tekshiruv.
    """
    tl = (text or "").lower()
    keywords = [
        "zakaz holati",
        "zakaz xolati",
        "zakaz holat",
        "zakaz status",
        "status zakaz",
        "holat",
        "xolati",
        "qani zakaz",
        "qani zakazim",
        "заказ где",
        "где заказ",
        "отправили уже",
        "когда привезете",
        "когда доставите",
    ]
    return any(k in tl for k in keywords)


async def is_status_question(
    settings: Settings,
    text: str,
    context_messages: List[str] | None = None,
) -> bool:
    """
    Foydalanuvchi xabari zakaz holatini/statusini so'rayaptimi – yo'qmi, shuni aniqlaydi.
    Natija: True / False.

    - Agar settings.openai_enabled = False bo'lsa → oddiy keyword-based.
    - Agar OpenAI yoki parsingda xatolik bo'lsa → fallback ham keyword-based.
    """
    if not text or not text.strip():
        return False

    # OpenAI ishlamasa → faqat keyword
    if not getattr(settings, "openai_enabled", False):
        return _simple_status_rule_based(text)

    if context_messages is None:
        context_messages = []

    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)

        system_prompt = """
Siz Telegram zakaz botiga yordam beradigan klassifikatorsiz.
Vazifangiz – foydalanuvchi xabaridan shuni aniqlash:
u zakaz holatini/statusini so'rayaptimi yoki yo'q.

"Status so'rash" misollari:
- "zakaz holati"
- "holat qanday"
- "qani zakazim"
- "заказ где?"
- "отправили уже?"
- "когда привезете?"
- "zakaz keldi mi?"
- "zakaz qachon keladi?"

"Status so'ramaydigan" xabarlar:
- "yana zakaz qilaman"
- "menu yuboring"
- "raqamim o'zgardi"
- "salom", "rahmat" va hokazo.

Faqat quyidagi JSON formatda javob qaytaring:

{
  "is_status": true yoki false
}
        """.strip()

        user_prompt = (
            "Kontekst xabarlar (agar bo'lsa, oxirgi 5 ta):\n"
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

        return bool(data.get("is_status", False))
    except Exception as e:
        print("Status intent OpenAI xato, rule-basedga qaytyapman:", repr(e))
        return _simple_status_rule_based(text)
