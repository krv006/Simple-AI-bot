# bot/ai/voice_order_structured.py
import json
from typing import List, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from bot.config import Settings
from .prompt_manager import load_prompt_config


class VoiceOrderExtraction(BaseModel):
    """
    STT'dan olingan voice xabar bo'yicha yakuniy strukturali natija.
    """
    is_order: bool = Field(
        ...,
        description="Xabar zakazga aloqador bo'lsa True, aks holda False.",
    )
    phone_numbers: List[str] = Field(
        default_factory=list,
        description=(
            "Faqat mijoz telefon raqamlari. Har biri +998 bilan boshlovchi to'liq raqam, "
            "masalan: +998901234567. Agar aniq bo'lmasa bo'sh qoldir."
        ),
    )
    amount: Optional[int] = Field(
        default=None,
        description=(
            "Zakaz summasi so'mda. Masalan, 'besh yuz ming so'm' -> 500000. "
            "Agar aniq summa yo'q bo'lsa, None."
        ),
    )
    comment: str = Field(
        ...,
        description=(
            "Kuryer uchun qisqa izoh. Masalan, mijozning og'zaki izohi, "
            "yoki xabarni tartiblangan ko'rinishda."
        ),
    )


def _escape_braces(text: str) -> str:
    """
    ChatPromptTemplate ichida literal { } ishlatish uchun
    ularni {{ }} ga almashtiramiz.
    """
    if not text:
        return text
    return text.replace("{", "{{").replace("}", "}}")


def _build_prompt() -> ChatPromptTemplate:
    """
    AI-ga aniq instruksiya beradigan prompt.
    Qoidalar prompt_config.json dan olinadi.
    """
    config, config_hash = load_prompt_config()
    rules = config.get("rules", {})
    examples = config.get("examples", [])
    output_schema = config.get("output_schema", {})

    system_parts: list[str] = []

    meta = config.get("meta", {})
    desc = meta.get("description")
    if desc:
        system_parts.append(_escape_braces(desc))

    system_parts.append(
        _escape_braces(
            "Siz Telegram dostavka botining AI yordamchisiz. "
            "Sizga STT (speech-to-text) orqali olingan xabar matni va "
            "rule-based topilgan telefon/summa nomzodlari beriladi. "
            "Siz yakuniy strukturali natijani to'g'ri va ishonchli qilishingiz kerak."
        )
    )

    # RULES bo'limini qo'shamiz
    for section, items in rules.items():
        system_parts.append(_escape_braces(f"\n[{section.upper()} QOIDALARI]:"))
        for rule in items:
            system_parts.append(_escape_braces(f"- {rule}"))

    # output_schema ni JSON ko'rinishida qo'shamiz (lekin {} larni escape qilamiz)
    if output_schema:
        schema_json = json.dumps(output_schema, ensure_ascii=False, indent=2)
        system_parts.append(
            _escape_braces(
                "\nChiqarilishi kerak bo'lgan JSON struktura tavsifi (output_schema):"
            )
        )
        system_parts.append(_escape_braces(schema_json))

    # examples larni ham qo'shamiz
    if examples:
        system_parts.append(_escape_braces("\nMisollar (input -> expected_output):"))
        for ex in examples[:3]:
            inp = ex.get("input", "")
            expected = ex.get("expected_output", {})
            expected_json = json.dumps(expected, ensure_ascii=False)

            system_parts.append(_escape_braces(f"Input:\n{inp}"))
            system_parts.append(
                _escape_braces("Expected JSON:\n" + expected_json)
            )

    system_msg = "\n".join(system_parts)

    # Human xabar – faqat uchta placeholder: text, raw_phone_candidates, raw_amount_candidates
    human_msg = (
        "Asosiy ma'lumotlar:\n"
        "STT matn yoki xabar matni: \"{text}\"\n\n"
        "Raw telefon kandidatlari (rule-based): {raw_phone_candidates}\n"
        "Raw summa kandidatlari (rule-based): {raw_amount_candidates}\n\n"
        "Yuqoridagi ma'lumotlar asosida VoiceOrderExtraction strukturasiga mos "
        "aniq natija qaytaring."
    )

    return ChatPromptTemplate.from_messages(
        [
            ("system", system_msg),
            ("human", human_msg),
        ]
    )


def get_voice_order_extractor(settings: Settings) -> ChatOpenAI:
    """
    LangChain ChatOpenAI modelini qaytaradi.
    """
    model = ChatOpenAI(
        model="gpt-4.1-mini",  # yoki siz ishlatayotgan model
        temperature=0,
        openai_api_key=settings.openai_api_key,
    )
    return model


def extract_order_structured(
        settings: Settings,
        *,
        text: str,
        raw_phone_candidates: list[str],
        raw_amount_candidates: list[int],
) -> VoiceOrderExtraction:
    """
    STT matn + rule-based nomzodlardan foydalanib,
    LangChain structured output orqali yakuniy natijani oladi.
    """
    prompt = _build_prompt()
    llm = get_voice_order_extractor(settings)
    structured_llm = llm.with_structured_output(VoiceOrderExtraction)

    chain = prompt | structured_llm

    result: VoiceOrderExtraction = chain.invoke(
        {
            "text": text,
            "raw_phone_candidates": raw_phone_candidates,
            "raw_amount_candidates": raw_amount_candidates,
        }
    )

    return result
