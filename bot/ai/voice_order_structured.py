# bot/ai/voice_order_structured.py
from typing import List, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from bot.config import Settings


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


def _build_prompt() -> ChatPromptTemplate:
    """
    AI-ga aniq instruksiya beradigan prompt.
    """
    system_msg = (
        "Siz Telegram dostavka botining AI yordamchisiz. "
        "Sizga STT (speech-to-text) orqali olingan xabar matni va "
        "qoida asosida taxmin qilingan telefon/summa nomzodlari beriladi.\n\n"
        "Sizning vazifangiz: yakuniy strukturali natijani to'g'ri va ishonchli qilish.\n\n"
        "QOIDALAR:\n"
        "1) Xabar O'ZBEK tilidagi og'zaki raqamlar va summalar bo'lishi mumkin.\n"
        "2) Telefon raqami odatda 9 xonali raqam (masalan, 901078055), lekin "
        "yakuniy natijada +998 bilan yozishingiz kerak: +998901078055.\n"
        "3) Agar matnda telefon raqam so'z bilan aytilgan bo'lsa (masalan, "
        "'yigirmalik nol nol nol o'n besh yigirma besh'), telefon sifatida "
        "shu raqamni yig'ib chiqishingiz kerak. Summani telefon bilan aralashtirmang.\n"
        "   - Masalan: 'yigirmalik nol nol nol o'n besh yigirma besh summasi besh yuz ming' "
        "bo'lsa, telefon: +998200015255, summa: 500000.\n"
        "4) Summani aniqlashda 'so'm', 'soum', 'сум' so'zlariga e'tibor bering. "
        "Masalan: 'besh yuz ming so'm' -> 500000.\n"
        "5) Agar birinchi raqamlar telefon raqami bo'lsa, keyingi raqamlar summa bo'lishi mumkin. "
        "Telefon va summani aralashtirmang.\n"
        "6) phone_numbers faqat mijozni chaqirish uchun kerak bo'lgan telefon(lar). "
        "Shop, reklama, yoki boshqa raqamlarni kiritmang.\n"
        "7) comment maydoniga mijoz so'zlarini qisqa, tushunarli ko'rinishda yozing. "
        "Address/region/mahalla, qo'shimcha so'zlar ham shu yerga kirishi mumkin.\n"
        "8) Agar xabar umuman zakaz emas bo'lsa (faqat 'Salom', "
        "'rahmat' va h.k.), is_order=False qiling, phone_numbers bo'sh, amount=None.\n"
    )

    human_msg = (
        "Asosiy ma'lumotlar:\n"
        "STT matn: \"{text}\"\n\n"
        "Raw telefon kandidatlari (rule-based): {raw_phone_candidates}\n"
        "Raw summa kandidatlari (rule-based): {raw_amount_candidates}\n\n"
        "Yuqoridagi ma'lumotlar asosida VoiceOrderExtraction strukturasi bo'yicha "
        "aniq va to'g'ri natijani qaytaring."
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
