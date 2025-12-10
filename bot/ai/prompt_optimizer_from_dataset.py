# bot/ai/prompt_optimizer_from_dataset.py

import json
from typing import Any, Dict, List

from .prompt_manager import load_prompt_config, save_prompt_config
from ..config import Settings
from ..db import load_orders_for_prompt_dataset
from .llm import call_llm_as_json   # Sizdagi OpenAI wrapper


def load_dataset_cases_from_db(
    settings: Settings,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """
    ai_orders jadvalidan prompt optimizer uchun misollarni oladi.
    """
    return load_orders_for_prompt_dataset(settings, limit=limit)


def optimize_prompt_from_dataset(
    settings: Settings,
    limit: int = 200,
) -> None:
    """
    Datasetni bevosita DB'dan olib, prompt_config.json ni yangilaydi.
    """
    current_config, config_hash = load_prompt_config()
    cases = load_dataset_cases_from_db(settings, limit=limit)

    if not cases:
        raise RuntimeError("DB'dan dataset topilmadi, ai_orders bo'sh yoki limit juda kichik.")

    user_prompt = f"""
Siz Telegram zakaz bot uchun PROMPT ENGINEER sifatida ishlayapsiz.

Quyida:
1) Hozirgi prompt_config.json (rules + examples)
2) Real DB'dan olingan misollar (raw_text + to‘g‘ri natijalar)

Vazifa:
- Qoidalarga aniq, kerakli o‘zgartirishlar kiriting.
- Telefonni noto‘g‘ri aniqlashga olib keladigan so'zlarni to'g'rilang
  (masalan, 'to'qsonlik', 'yetmishlik', 'to'qson birlik' – telefon emas).
- Kerak bo'lsa, examples bo'limiga yangi misollar qo‘shing.
- Keraksiz, haddan tashqari murakkab qoidalar qo‘shmang.

Hozirgi prompt_config:
{json.dumps(current_config, ensure_ascii=False, indent=2)}

DB'dan olingan dataset misollari:
{json.dumps(cases, ensure_ascii=False, indent=2)}

Yangi prompt_config'ni faqat toza JSON qilib qaytaring.
Izoh yozmang.
    """

    new_config = call_llm_as_json(
        settings=settings,
        system_prompt=(
            "Siz professional prompt engineer bo'lib, "
            "faqat yaroqli JSON konfiguratsiya qaytarasiz."
        ),
        user_prompt=user_prompt,
    )

    save_prompt_config(new_config)
    print("✅ prompt_config.json DB'dagi dataset asosida yangilandi.")
