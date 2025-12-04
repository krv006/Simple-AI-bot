# bot/utils/amounts.py
from __future__ import annotations

import logging
import re
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)

# Uzbek son so'zlari
UNITS = {
    "nol": 0,
    "bir": 1,
    "ikki": 2,
    "uch": 3,
    "tort": 4,
    "to'rt": 4,
    "turt": 4,
    "besh": 5,
    "olti": 6,
    "yetti": 7,
    "sakkiz": 8,
    "toqqiz": 9,
}

TENS = {
    "on": 10,
    "yigirma": 20,
    "ottiz": 30,
    "o'ttiz": 30,
    "qirq": 40,
    "ellik": 50,
    "oltmish": 60,
    "yetmish": 70,
    "sakson": 80,
    "to'qson": 90,
    "toqson": 90,
    "to'qsonlik": 90,
    "toqsonlik": 90,
}

SCALES = {
    "yuz": 100,
    "ming": 1000,
    "million": 1_000_000,
    "mln": 1_000_000,
}

# Summaga oid kalit so'zlar – kontekstni baholash uchun
MONEY_KEYWORDS = [
    "summa",
    "sum",
    "so'm",
    "som",
    "сум",
    "сом",
    "тыс",
    "ming",
    "минг",
    "min",  # "25 min" – 25 ming deb ishlatilishi mumkin
]


def _normalize_token(w: str) -> str:
    w = w.lower()
    w = (
        w.replace("’", "'")
        .replace("`", "'")
        .replace("‘", "'")
        .replace("ʼ", "'")
    )
    return w


def _parse_number_phrase(tokens: List[str]) -> int:
    """
    'ikki yuz ellik ming' -> 250000
    'uch yuz ming' -> 300000
    """
    total = 0
    current = 0

    for raw in tokens:
        w = _normalize_token(raw)

        if w in UNITS:
            current += UNITS[w]
        elif w in TENS:
            current += TENS[w]
        elif w in SCALES:
            scale = SCALES[w]
            if current == 0:
                current = 1
            current *= scale
            if scale >= 1000:
                total += current
                current = 0
        elif re.fullmatch(r"\d+([\.,]\d+)?", w):
            # raqamli token (300, 300.5 va hokazo)
            val = float(w.replace(",", "."))
            current += val
        # boshqa so'zlarni e'tiborsiz qoldiramiz

    return int(total + current)


def _extract_yuz_ming_candidates(text: str) -> List[Tuple[int, List[str]]]:
    """
    Matndan '... uch yuz ming ...', 'ikki yuz ellik ming ...' kabi
    yuz+ming strukturalarini topib, raqamga aylantirib qaytaradi.
    """
    cleaned = re.sub(r"[^\w\s'ʼ`’]", " ", text.lower())
    tokens = re.split(r"\s+", cleaned.strip())
    candidates: List[Tuple[int, List[str]]] = []

    for j, tok in enumerate(tokens):
        if _normalize_token(tok) == "ming":
            # oldindan eng yaqin 'yuz' ni topamiz
            yuz_idx = None
            for k in range(j - 1, -1, -1):
                if _normalize_token(tokens[k]) == "yuz":
                    yuz_idx = k
                    break
            if yuz_idx is None:
                continue

            # 'uch yuz ming' bo'lsin deb bitta tokenni oldindan ham olamiz
            start = max(0, yuz_idx - 1)
            phrase_tokens = tokens[start: j + 1]
            value = _parse_number_phrase(phrase_tokens)
            if value > 0:
                candidates.append((value, phrase_tokens))

    return candidates


def _looks_like_phone(digits: str) -> bool:
    """
    Telefon raqamga o'xshagan sonlarni filtr qilish:
    - uzunligi 9–12 bo'lsa
    - va 998 / 9 / 8 bilan boshlansa
    """
    if not digits:
        return False

    length = len(digits)

    # 998 bilan boshlanadigan 12 xonali son – O'zbek telefoni
    if length >= 11 and digits.startswith("998"):
        return True

    # 9, 10, 11, 12 xonali va 8/9 bilan boshlansa – telefon bo'lish ehtimoli katta
    if length in (9, 10, 11, 12) and digits.startswith(("9", "8")):
        return True

    return False


def extract_amount_from_text(text: str) -> Optional[int]:
    """
    Berilgan matndan ehtimoliy summa (so'm) ni integer ko'rinishida qaytaradi.

    Misollar:
      "uch yuz ming so'm" -> 300000
      "ikki yuz ellik ming" -> 250000
      "300 ming" -> 300000
      "Bahodir 983373630 ... 277 000 ... 25 min" -> 277000

    Telefon ko'rinishidagi sonlar e'tiborsiz qoldiriladi.
    Bir nechta kandidat bo'lsa, (score, value) bo'yicha eng yaxshisi tanlanadi.
    """
    if not text:
        return None

    cleaned = text.replace("\u00a0", " ")

    candidates: List[Tuple[int, int]] = []  # (value, score)

    # 1) '... yuz ... ming' strukturalari – bu deyarli har doim summa
    for value, phrase_tokens in _extract_yuz_ming_candidates(cleaned):
        if value > 0:
            score = 5  # yuqori ishonch
            candidates.append((value, score))
            logger.debug(
                "Phrase-based amount candidate: value=%s tokens=%r score=%s",
                value,
                phrase_tokens,
                score,
            )

    # 2) Raqamli ko'rinishlar (300000, 12 000, 300 ming, 25 min va hokazo)
    for m in re.finditer(r"\d[\d\s]*", cleaned):
        raw_token = m.group()
        digits = re.sub(r"\D", "", raw_token)

        if not digits:
            continue

        # Telefon ko'rinishidagilarni tashlab yuboramiz
        if _looks_like_phone(digits):
            logger.debug("Skipping phone-like number in amount extraction: %s", digits)
            continue

        try:
            base_value = int(digits)
        except ValueError:
            continue

        start, end = m.start(), m.end()
        window_start = max(0, start - 25)
        window_end = min(len(cleaned), end + 25)
        window = cleaned[window_start:window_end].lower()

        score = 0

        # Son atrofida pulga oid so'zlar bo'lsa – ball oshadi
        if any(kw in window for kw in MONEY_KEYWORDS):
            score += 3

        # "300 ming", "25 min" – kichik son + "ming/min" bo'lsa, 1000 ga ko'paytiramiz
        multiplier = 1
        if base_value < 1000 and any(kw in window for kw in ("ming", "минг", "min")):
            multiplier = 1000
            score += 2  # bu summaga juda o'xshaydi

        value = base_value * multiplier

        # 3 xonali va kattaroq bo'lsa, summaga o'xshash
        if value >= 1000:
            score += 2

        # Juda kichik sonlar (1–99) – odatda miqdor/vaqt, summadan ko'ra kamroq
        if 0 < value < 100:
            score -= 1

        logger.debug(
            "Digit-based amount candidate: raw=%r digits=%s value=%s score=%s window=%r",
            raw_token,
            digits,
            value,
            score,
            window,
        )

        candidates.append((value, score))

    if not candidates:
        return None

    # Avval score bo'yicha, keyin value bo'yicha saralaymiz
    candidates.sort(key=lambda x: (x[1], x[0]), reverse=True)
    best_value, best_score = candidates[0]

    logger.info(
        "Selected amount from text: value=%s score=%s text=%r",
        best_value,
        best_score,
        text,
    )

    return best_value
