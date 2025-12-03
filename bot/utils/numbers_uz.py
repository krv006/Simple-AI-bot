# bot/utils/numbers_uz.py
import logging
import re
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


def _norm(w: str) -> str:
    """
    Apostroflarni bir xil ko'rinishga keltiramiz va lower().
    """
    w = w.lower()
    w = (
        w.replace("’", "'")
        .replace("`", "'")
        .replace("‘", "'")
        .replace("ʼ", "'")
    )
    return w


UNITS = {
    "nol": 0,
    "nolik": 0,
    "bir": 1,
    "ikki": 2,
    "uch": 3,
    "tort": 4,
    "to'rt": 4,
    "turt": 4,
    "besh": 5,
    "olti": 6,
    "yetti": 7,
    "etti": 7,
    "sakkiz": 8,
    "toqqiz": 9,
    "to'qqiz": 9,
    "toqiz": 9,
}

TENS = {
    "on": 10,
    "yigirma": 20,
    "ottiz": 30,
    "o'ttiz": 30,
    "otiz": 30,
    "qirq": 40,
    "ellik": 50,
    "oltmish": 60,
    "yetmish": 70,
    "sakson": 80,
    "to'qson": 90,
    "toqson": 90,
}

SCALES = {
    "yuz": 100,
    "ming": 1_000,
    "million": 1_000_000,
    "milliom": 1_000_000,  # STT xatolari uchun
    "milliard": 1_000_000_000,
    "mln": 1_000_000,
}


def _parse_number_tokens(tokens: List[str], start: int) -> Tuple[Optional[int], int]:
    """
    tokens[start:] dan boshlab uzbekcha son so'zlar ketma-ketligini integer ga aylantiradi.

    Return:
      (value, used_count)
      agar son topilmasa: (None, 0)
    """
    total = 0
    current = 0
    used = 0
    met_any = False  # Hech bo'lmaganda bitta son so'zini ko'rdikmi?

    i = start
    while i < len(tokens):
        w_norm = _norm(tokens[i])

        if w_norm in UNITS:
            current += UNITS[w_norm]
            met_any = True
            used += 1
            i += 1
            continue

        if w_norm in TENS:
            current += TENS[w_norm]
            met_any = True
            used += 1
            i += 1
            continue

        if w_norm in SCALES:
            scale = SCALES[w_norm]
            if current == 0:
                current = 1
            current *= scale

            # ming/million kabi so'zlar segment yakuni bo'lishi mumkin
            if scale >= 1000:
                total += current
                current = 0

            met_any = True
            used += 1
            i += 1
            continue

        # Boshqa so'z keldi – son ketma-ketligi tugadi
        break

    if not met_any:
        return None, 0

    total += current
    return total, used


def _tokenize_text(text: str) -> List[str]:
    """
    Matnni oddiy bo'shliqlar orqali tokenlarga bo'lamiz,
    lekin apostroflarni yo'qotmaymiz.
    """
    cleaned = re.sub(r"[^\w\s'ʼ`’]", " ", text or "")
    tokens = [t for t in re.split(r"\s+", cleaned) if t]
    return tokens


def normalize_uzbek_numbers_in_text(text: str) -> str:
    """
    Matndan uzbekcha son so'zlar ketma-ketligini topib, o'rniga raqam yozadi.

    Misollar:
      "uch yuz to'qqiz"        -> "309"
      "ikki yuz o'n besh ming" -> "215000"
      "bir ming uch yuz ellik" -> "1350"
    """
    if not text:
        return text

    tokens = _tokenize_text(text)
    result_tokens: List[str] = []
    i = 0

    while i < len(tokens):
        value, used = _parse_number_tokens(tokens, i)
        if value is not None and used > 0:
            result_tokens.append(str(value))
            i += used
        else:
            result_tokens.append(tokens[i])
            i += 1

    new_text = " ".join(result_tokens)
    logger.info("[NUMBERS_UZ] text=%r -> %r", text, new_text)
    return new_text


# ===== Summa chiqarish =====

_AMOUNT_WITH_CURRENCY = re.compile(
    r"(\d+)\s*(so['`ʼ’]m|som|sum|сум)",
    re.IGNORECASE,
)


def extract_amount_from_text(text: str) -> Optional[int]:
    """
    STT matndan summani integer ko'rinishida chiqaradi.
    Avval uzbekcha sonlarni raqamga aylantiradi, keyin raqamlarni qidiradi.

      "besh yuz ming so'm" -> 500000
      "uch yuz to'qqiz mingga" -> 309000 (agar shunday kontekst bo'lsa)

    Strategiya:
      1) normalize_uzbek_numbers_in_text()
      2) so'm/сум yonidagi raqamlarni qidirish
      3) topilmasa – matndagi eng katta raqamni olish
    """
    if not text:
        return None

    normalized = normalize_uzbek_numbers_in_text(text)

    # 1) Valyuta bilan yozilgan raqamlar
    matches = _AMOUNT_WITH_CURRENCY.findall(normalized)
    if matches:
        nums = [int(m[0]) for m in matches]
        amount = max(nums)
        logger.info("[AMOUNT] normalized=%r -> from currency=%s -> %s", normalized, nums, amount)
        return amount

    # 2) Fallback: matndagi barcha raqamlar ichidan eng kattasini olish
    all_nums = re.findall(r"\d+", normalized)
    if not all_nums:
        logger.info("[AMOUNT] normalized=%r -> no digits found", normalized)
        return None

    nums_int = [int(n) for n in all_nums]
    amount = max(nums_int)
    logger.info("[AMOUNT] normalized=%r -> from digits=%s -> %s", normalized, nums_int, amount)
    return amount
