# bot/prompt_seed.py
import json
import logging
import os

from .config import Settings
from .db import get_active_prompt_config, create_prompt_config

logger = logging.getLogger(__name__)


def seed_prompt_if_needed(settings: Settings) -> None:
    """
    Agar ai_prompt_configs jadvalida active prompt yo'q bo'lsa,
    bot/prompt_seed.json faylidan promptni o'qib, DB ga yozadi.
    Fayl topilmasa – xato ko'tarmaydi, faqat warning chiqaradi.
    """
    existing = get_active_prompt_config(settings)
    if existing:
        logger.info("seed_prompt_if_needed: active prompt_config allaqachon bor, seed qilinmaydi.")
        return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    filename = os.path.join(base_dir, "prompt_seed.json")

    if not os.path.exists(filename):
        logger.warning("seed_prompt_if_needed: %s topilmadi, seed o'tkazib yuborildi.", filename)
        return

    try:
        with open(filename, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        logger.exception("seed_prompt_if_needed: %s ni o'qishda xatolik: %s", filename, e)
        return

    try:
        row = create_prompt_config(
            settings=settings,
            payload=payload,
            source="auto_seed",
            make_active=True,
        )
        logger.info(
            "seed_prompt_if_needed: prompt_config DB ga yozildi. id=%s, version=%s",
            row["id"],
            row["version"],
        )
    except Exception as e:
        logger.exception("seed_prompt_if_needed: DB ga yozishda xatolik: %s", e)
