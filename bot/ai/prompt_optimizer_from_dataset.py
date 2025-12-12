# bot/ai/prompt_optimizer_from_dataset.py

import hashlib
import json
from typing import Any, Dict, List

from .llm import call_llm_as_json  # Sizdagi OpenAI wrapper
from .prompt_manager import load_prompt_config, save_prompt_config
from ..config import Settings
from ..db import load_orders_for_prompt_dataset

TOP_LEVEL_KEYS = ["version", "meta", "rules", "output_schema", "examples"]


def load_dataset_cases_from_db(
        settings: Settings,
        limit: int = 200,
) -> List[Dict[str, Any]]:
    """
    ai_orders jadvalidan prompt optimizer uchun misollarni oladi.
    """
    return load_orders_for_prompt_dataset(settings, limit=limit)


def _stable_example_key(ex: Any) -> str:
    if isinstance(ex, dict):
        raw = (ex.get("input") or "") + "||" + (ex.get("output") or "")
    else:
        raw = str(ex)

    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"ex:{h}"


def _build_prompt_patch(
        old_config: Dict[str, Any],
        new_config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Faqat AI qo'shgan / o'zgartirgan qismlarni qaytaradi:
      - changed_rules: rules ichidagi o'zgargan yoki yangi keylar
      - new_examples: old_configda bo'lmagan examples
      - removed_examples: old_configda bor, new_configda yo'q examples (xohlasangiz ko'rsatish uchun)
    """
    old_rules = old_config.get("rules") or {}
    new_rules = new_config.get("rules") or {}

    changed_rules: Dict[str, Any] = {}
    for k, v in new_rules.items():
        if old_rules.get(k) != v:
            changed_rules[k] = v

    old_examples = old_config.get("examples") or []
    new_examples = new_config.get("examples") or []

    old_map = {_stable_example_key(x): x for x in old_examples}
    new_map = {_stable_example_key(x): x for x in new_examples}

    added_keys = [k for k in new_map.keys() if k not in old_map]
    removed_keys = [k for k in old_map.keys() if k not in new_map]

    patch = {
        "changed_rules": changed_rules,
        "new_examples": [new_map[k] for k in added_keys],
        "removed_examples": [old_map[k] for k in removed_keys],
    }
    return patch


def _validate_new_config(
        old_config: Dict[str, Any],
        new_config: Dict[str, Any],
) -> None:
    old_keys = set(old_config.keys())
    new_keys = set(new_config.keys())

    expected = set(TOP_LEVEL_KEYS)

    if new_keys != expected:
        raise RuntimeError(
            "Yangi config top-level keylari noto'g'ri. "
            f"Kutilgan: {sorted(expected)}, Kelgan: {sorted(new_keys)}"
        )

    # 2) output_schema must be identical
    if old_config.get("output_schema") != new_config.get("output_schema"):
        raise RuntimeError(
            "AI 'output_schema' ni o'zgartirib yuborgan. Bu taqiqlangan."
        )

    # 3) version/meta saqlanib qolishi shart emas (siz xohlasangiz shart qilsangiz bo'ladi),
    # lekin ko'p hollarda version/meta o'zgarishi ham kerak emas.
    # Agar xohlasangiz pastdagi tekshiruvlarni yoqing:
    # if old_config.get("version") != new_config.get("version"):
    #     raise RuntimeError("AI 'version' ni o'zgartirib yuborgan. Bu taqiqlangan.")
    # if old_config.get("meta") != new_config.get("meta"):
    #     raise RuntimeError("AI 'meta' ni o'zgartirib yuborgan. Bu taqiqlangan.")


def optimize_prompt_from_dataset(
        settings: Settings,
        limit: int = 200,
        save: bool = True,
) -> Dict[str, Any]:
    current_config, config_hash = load_prompt_config()
    cases = load_dataset_cases_from_db(settings, limit=limit)

    if not cases:
        raise RuntimeError(
            "DB'dan dataset topilmadi, ai_orders bo'sh yoki limit juda kichik."
        )

    user_prompt = f"""
Siz Telegram zakaz bot uchun PROMPT ENGINEER sifatida ishlayapsiz.

Quyida:
1) Hozirgi prompt_config.json (rules + output_schema + examples)
2) Real DB'dan olingan misollar (input + ground_truth)

Vazifa:
- Qoidalarga aniq, kerakli o‘zgartirishlar kiriting.
- Telefonni noto‘g‘ri aniqlashga olib keladigan so‘zlarni to‘g‘rilang
  (masalan, 'to'qsonlik', 'yetmishlik', 'to'qson birlik' – telefon emas).
- Kerak bo‘lsa, examples bo‘limiga yangi misollar qo‘shing.
- Keraksiz, haddan tashqari murakkab qoidalar qo‘shmang.

Cheklovlar (QATTIQ):
- Top-level kalitlar aynan shular bo‘lsin: {TOP_LEVEL_KEYS}
- "output_schema" ni O‘ZGARTIRMANG. Faqat "rules" va "examples" ichida ishlang.
- Telefonni yoki summani hech qachon taxmin qilmang – faqat matndagi aniq ma’lumotdan foydalaning.
- Natija faqat JSON bo‘lsin.

Sizning javobingiz JSON bo‘lib, aynan 2 ta kalitdan iborat bo‘lsin:
1) "new_config": yangilangan prompt_config (to'liq)
2) "rationale": NIMA SABABDAN shunday o'zgartirganingizni qisqa va amaliy tarzda tushuntiring.
   - 3–7 ta bullet (matn ko'rinishida)
   - Qaysi xatolarni kamaytiradi
   - Qaysi qoida / misol nimani tuzatadi
   - Butun promptni qayta yozmang, faqat sababni yozing

Hozirgi prompt_config:
{json.dumps(current_config, ensure_ascii=False, indent=2)}

DB'dan olingan dataset misollari:
{json.dumps(cases, ensure_ascii=False, indent=2)}
    """.strip()

    result = call_llm_as_json(
        settings=settings,
        system_prompt=(
            "Siz professional prompt engineer bo'lib, faqat yaroqli JSON qaytarasiz. "
            "Hech qanday izoh, markdown, qo'shimcha matn yozmang."
        ),
        user_prompt=user_prompt,
    )

    if not isinstance(result, dict):
        raise RuntimeError("LLM natijasi dict bo'lishi kerak edi (JSON object).")

    new_config = result.get("new_config")
    rationale = result.get("rationale")

    if not isinstance(new_config, dict):
        raise RuntimeError("LLM 'new_config' ni noto'g'ri formatda qaytardi (dict emas).")

    if not isinstance(rationale, str):
        rationale = "—"

    _validate_new_config(current_config, new_config)

    patch = _build_prompt_patch(current_config, new_config)

    if save:
        save_prompt_config(new_config)
        print("✅ prompt_config.json DB'dagi dataset asosida yangilandi.")

    return {
        "old_config": current_config,
        "new_config": new_config,
        "patch": patch,
        "rationale": rationale,
    }
