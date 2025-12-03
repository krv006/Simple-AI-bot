# bot/utils/phones.py
import logging
import re
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

# Raqamli telefonlarni topish uchun regex (matndan)
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
    # Debug uchun konsolga ham chiqaramiz
    print(f"[PHONES] text={text!r} -> matches={matches} -> normalized={result}")

    return result


# ========== Og'zaki telefon raqamlari (so'z bilan aytilgan) ==========

# Og'zaki telefon uchun son so'zlar -> raqamlar.
# E’TIBOR: bu yerda "yuz", "ming" va h.k. telefon uchun FOYDALANILMAYDI.
DIGIT_WORDS_PHONE = {
    # birliklar
    "nol": "0",
    "nolik": "0",
    "bir": "1",
    "ikki": "2",
    "uch": "3",
    "tort": "4",
    "to'rt": "4",
    "turt": "4",
    "besh": "5",
    "olti": "6",
    "yetti": "7",
    "etti": "7",  # STT ko'p hollarda "etti" deb beradi
    "sakkiz": "8",
    "toqqiz": "9",
    "to'qqiz": "9",
    "toqiz": "9",

    # o'nliklar (telefon uchun 2 raqam sifatida ko'rib, shu holatda qoldiramiz)
    "on": "10",
    "yigirma": "20",
    "ottiz": "30",
    "o'ttiz": "30",
    "qirq": "40",
    "ellik": "50",
    "oltmish": "60",
    "yetmish": "70",
    "sakson": "80",
    "to'qson": "90",
    "toqson": "90",
    "to'qsonlik": "90",
    "toqsonlik": "90",
}


def _normalize_token(w: str) -> str:
    w = w.lower()
    w = (
        w.replace("’", "'")
        .replace("`", "'")
        .replace("‘", "'")
        .replace("ʼ", "'")
    )
    return w


def _postprocess_phone_digits(seq: str) -> Optional[str]:
    """
    Og'zaki son so'zlaridan yig'ilgan raqamlar ketma-ketligini
    telefon formatiga yaqinlashtirish uchun ishlov beramiz:
      - agar 9 dan uzun bo'lsa, oxirgi 9 raqamni olamiz
      - minimal uzunlik 5 raqam (uzoqroq gaplardan ham nimadir olish uchun)
    """
    if not seq:
        return None

    # Juda uzun bo'lsa – oxirgi 9 raqamni olamiz
    if len(seq) > 9:
        seq = seq[-9:]

    # Avval 7 edi, hozir 5 qilib, juda qattiq filterni yumshatdik
    if len(seq) < 5:
        return None

    return seq


def extract_spoken_phone_candidates(text: str) -> List[str]:
    """
    STT matndan so'z bilan aytilgan raqamlar ketma-ketligini raqamga aylantiradi.
    Bu yerda biz SON so'zlar ketma-ketligidan raqam zanjiri yig'amiz:

      "telefon raqami to'qson birlik bir yuz o'n bir o'n ikki oltmish uch"

    kabi gaplarda ham hech bo'lmaganda biror raqamli ketma-ketlik olishga harakat qilamiz.

    Natijada faqat raqamlardan iborat ketma-ketliklar qaytariladi, masalan: ["901780505"].
    """
    cleaned = re.sub(r"[^\w\s'ʼ`’]", " ", text or "")
    tokens = [t for t in re.split(r"\s+", cleaned) if t]

    digit_sequences: List[str] = []
    current_digits: List[str] = []

    def flush():
        nonlocal current_digits, digit_sequences
        if not current_digits:
            return
        raw_seq = "".join(current_digits)
        processed = _postprocess_phone_digits(raw_seq)
        if processed:
            digit_sequences.append(processed)
        current_digits = []

    for tok in tokens:
        w = _normalize_token(tok)

        if w in DIGIT_WORDS_PHONE:
            # Har bir son so'zini o'ziga tegishli raqam(lar)ga aylantiramiz
            current_digits.append(DIGIT_WORDS_PHONE[w])
            continue

        # "yuz", "ming", "million" va hokazo – telefon uchun tashlab yuboramiz
        if w in {"yuz", "ming", "million", "mln"}:
            continue

        # Boshqa so'z bo'lsa – ketma-ketlikni yakunlaymiz
        flush()

    flush()

    # Duplicatlarni olib tashlaymiz
    unique: List[str] = []
    for seq in digit_sequences:
        if seq not in unique:
            unique.append(seq)

    logger.info("[SPOKEN_PHONES] text=%r -> digit_seqs=%s", text, unique)
    return unique


# ========== Display uchun formatlash (90 107 80 55) ==========

def format_phone_display(phone: str) -> str:
    """
    Telefonni ko‘rinadigan formatga bo‘ladi.
    +998901078055 -> 901078055 -> "90 107 80 55"
    """
    digits = re.sub(r"\D", "", phone or "")

    # 998 bilan boshlansa → oxirgi 9 raqamni olamiz
    if digits.startswith("998") and len(digits) >= 12:
        digits = digits[-9:]

    if len(digits) != 9:
        return phone

    return f"{digits[0:2]} {digits[2:5]} {digits[5:7]} {digits[7:9]}"
