# bot/utils/phones.py
import logging
import re
from typing import List, Optional, Set

from bot.utils.numbers_uz import spoken_phone_words_to_digits

logger = logging.getLogger(__name__)

PHONE_REGEX = re.compile(r"(\+?\d(?:[ \-\(\)]*\d){7,})")


def normalize_phone(raw: str) -> Optional[str]:
    """
    Matndan olingan xom telefon satrini normalize qiladi:

      "97 777 77 77"      -> +998977777777
      "+998901234567"     -> +998901234567
      "901234567"         -> +998901234567
      "178033075"         -> +998178033075  (og'zaki telefonlardan)
    """
    digits = re.sub(r"\D", "", raw or "")

    if len(digits) < 9:
        return None

    # 998 bilan boshlanadigan to'liq o'zbek raqami
    if digits.startswith("998") and len(digits) == 12:
        return f"+{digits}"

    # 9 xonali mahalliy raqam (90xxxxxxx, 97xxxxxxx, 178033075 va h.k.)
    if len(digits) == 9:
        return f"+998{digits}"

    # Aks holda: uzoqroq bo'lsa ham + bilan qaytaramiz
    return f"+{digits}"


def extract_phones(text: str) -> List[str]:
    """
    Matndan raqamli telefonlarni topib, normalize qiladi.
    (STT matn, oddiy text xabarlar va h.k. uchun.)
    """
    if not text:
        return []

    matches = PHONE_REGEX.findall(text)
    normalized: Set[str] = set()

    for m in matches:
        p = normalize_phone(m)
        if p:
            normalized.add(p)

    result = list(normalized)

    logger.info(
        "[PHONES] text=%r -> matches=%s -> normalized=%s",
        text,
        matches,
        result,
    )
    print(f"[PHONES] text={text!r} -> matches={matches} -> normalized={result}")

    return result


# ========== Og'zaki telefon raqamlari (so'z bilan aytilgan) ==========


def _postprocess_phone_digits(seq: str) -> Optional[str]:
    """
    Og'zaki son so'zlaridan yig'ilgan raqamlar ketma-ketligini
    telefon formatiga yaqinlashtirish uchun ishlov beramiz:

      - agar 9 dan uzun bo'lsa, BIRINCHI 9 raqamni olamiz
      - minimal uzunlik 9 raqam (to'liq o'zbek nomer)
    """
    if not seq:
        return None

    # Minimal – to'liq o'zbek nomer uzunligi (9 ta raqam)
    if len(seq) < 9:
        return None

    # Juda uzun bo'lsa – birinchi 9 raqamni olamiz (oxiridagi summa va h.k. larni kesib tashlaymiz)
    if len(seq) > 9:
        seq = seq[:9]

    return seq


def extract_spoken_phone_candidates(text: str) -> List[str]:
    """
    STT matndan so'z bilan aytilgan raqamlar ketma-ketligini raqamga aylantiradi.
    Bu yerda biz bot.utils.numbers_uz.spoken_phone_words_to_digits funksiyasidan
    foydalanamiz (siz yozgan logic asosida).

    Misollar:
      "to'qsonlik bir yuz etti sakson ellik besh"
        -> spoken_phone_words_to_digits(...) = "901078055"
        -> ["901078055"]

      "tezkur yol kerak to'qsonlik bir yuz yetti sakson lik besh raqamiga besh yuz ming so'm chilonzor"
        -> spoken_phone_words_to_digits(...) taxminan "901078055500"
        -> _postprocess_phone_digits(...) -> "901078055"
        -> ["901078055"]
    """
    if not text:
        return []

    digit_str = spoken_phone_words_to_digits(text)
    digit_str = re.sub(r"\D", "", digit_str or "")

    if not digit_str:
        logger.info("[SPOKEN_PHONES] text=%r -> no digit_str", text)
        return []

    processed = _postprocess_phone_digits(digit_str)
    if not processed:
        logger.info(
            "[SPOKEN_PHONES] text=%r -> digit_str=%r is too short/invalid",
            text,
            digit_str,
        )
        return []

    logger.info(
        "[SPOKEN_PHONES] text=%r -> digit_str=%r -> %s",
        text,
        digit_str,
        processed,
    )
    return [processed]


def format_phone_display(phone: str) -> str:
    """
    +998901078055 -> 901078055 -> "90 107 80 55"
    """
    digits = re.sub(r"\D", "", phone or "")

    if digits.startswith("998") and len(digits) >= 12:
        digits = digits[-9:]

    if len(digits) != 9:
        return phone

    return f"{digits[0:2]} {digits[2:5]} {digits[5:7]} {digits[7:9]}"
