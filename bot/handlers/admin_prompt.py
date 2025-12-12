# bot/handlers/admin_prompt.py
import copy
import html
import json
import logging
from typing import Any, Dict, List, Set

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message

from ..ai.prompt_optimizer_from_dataset import optimize_prompt_from_dataset
from ..config import Settings
from ..db import (
    create_prompt_config,
    get_active_prompt_config,
)

logger = logging.getLogger(__name__)

ADMIN_IDS = {1305675046}
PROMPT_DEBUG_CHAT_ID = -5030824970


def _build_prompt_diff_payload(
    old_config: Dict[str, Any],
    new_config: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Eski va yangi prompt_config orasidagi farqni tayyorlaydi:
    - changed_rules: faqat o'zgargan rules bo'limlari
    - new_examples: eski configda bo'lmagan misollar
    """
    old_rules = (old_config.get("rules") or {}) if isinstance(old_config, dict) else {}
    new_rules = (new_config.get("rules") or {}) if isinstance(new_config, dict) else {}

    changed_rules: Dict[str, Dict[str, List[str]]] = {}

    for section, new_list in new_rules.items():
        old_list = old_rules.get(section, [])
        # Listlar farq qilsa – bu bo'limni o'zgargan deb olamiz
        if new_list != old_list:
            changed_rules[section] = {
                "old": old_list,
                "new": new_list,
            }

    # Examples bo'yicha: input bo'yicha yangi misollarni topamiz
    old_examples = old_config.get("examples") or []
    new_examples = new_config.get("examples") or []

    old_inputs: Set[str] = set()
    for ex in old_examples:
        if isinstance(ex, dict):
            inp = ex.get("input")
            if isinstance(inp, str):
                old_inputs.add(inp)

    new_examples_only: List[Dict[str, Any]] = []
    for ex in new_examples:
        if not isinstance(ex, dict):
            continue
        inp = ex.get("input")
        if isinstance(inp, str) and inp not in old_inputs:
            new_examples_only.append(ex)

    return {
        "changed_rules": changed_rules,
        "new_examples": new_examples_only,
    }


def _extract_payload(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    get_active_prompt_config ikki xil ko'rinishda bo'lishi mumkin:
      1) faqat payload (rules, examples va h.k.)
      2) yoki {"id":..., "payload": {...}, "version":...}

    Har ikkala holatga moslashish uchun payloadni ajratib olamiz.
    """
    if not isinstance(cfg, dict):
        return {}

    inner = cfg.get("payload")
    if isinstance(inner, dict):
        return inner

    # Agar ichida "payload" bo'lmasa – o'zi payload deb qabul qilamiz
    return cfg


def _save_new_prompt_version(
    settings: Settings,
    payload: Dict[str, Any],
    source: str = "manual_update",
) -> Dict[str, Any]:
    """
    Mavjud payload asosida yangi version yaratish.
    - payload ichidagi "version" ni +1 qilamiz (agar bo'lmasa 1 deb olamiz).
    - DBga create_prompt_config orqali yozamiz va active qilamiz.
    """
    new_payload = copy.deepcopy(payload)
    old_version = int(new_payload.get("version", 1))
    new_payload["version"] = old_version + 1

    row = create_prompt_config(
        settings=settings,
        payload=new_payload,
        source=source,
        make_active=True,
    )
    logger.info(
        "New prompt_config stored: id=%s, version=%s, source=%s",
        row["id"],
        row["version"],
        row["source"],
    )
    return row


def register_admin_prompt_handlers(dp: Dispatcher, settings: Settings) -> None:
    # =========================
    # 1) OPTIMIZER ORQALI YANGILASH
    # =========================
    @dp.message(Command("optimize_prompt"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_optimize_prompt(message: Message):
        logger.info(
            "Admin optimize_prompt: chat=%s from=%s(%s)",
            message.chat.id,
            message.from_user.id,
            message.from_user.username,
        )

        await message.answer("♻️ Prompt optimizatsiya qilinyapti...")

        try:
            result = optimize_prompt_from_dataset(
                settings=settings,
                limit=300,
            )
            old_config = result.get("old_config") or {}
            new_config = result.get("new_config") or {}

            # optimizerdan chiqqan payloadni DBga yozamiz va active qilamiz
            try:
                row = create_prompt_config(
                    settings=settings,
                    payload=new_config,
                    source="optimizer",
                    make_active=True,
                )
                logger.info(
                    "New prompt_config stored to DB via optimizer: id=%s, version=%s",
                    row["id"],
                    row["version"],
                )
            except Exception as e:
                logger.exception("Failed to store optimizer prompt_config to DB: %s", e)

            await message.answer(
                "✅ prompt_config yangilandi (optimizer) va DB'da active qilindi."
            )

            # Debug kanali uchun diff va izohlar
            try:
                diff_payload = _build_prompt_diff_payload(old_config, new_config)

                changed_rules = diff_payload["changed_rules"]
                new_examples = diff_payload["new_examples"]

                if not changed_rules and not new_examples:
                    await message.bot.send_message(
                        chat_id=PROMPT_DEBUG_CHAT_ID,
                        text=(
                            "ℹ️ /optimize_prompt ishga tushdi, "
                            "lekin rules/examples'da sezilarli o'zgarish yo'q."
                        ),
                    )
                    return

                changed_sections_list = list(changed_rules.keys())
                changed_sections_str = (
                    ", ".join(changed_sections_list) if changed_sections_list else "—"
                )
                new_examples_count = len(new_examples)

                reason_text = (
                    "<b>Qisqa izoh:</b>\n"
                    f"- O'zgargan rules bo'limlari: {html.escape(changed_sections_str)}\n"
                    f"- Yangi misollar soni: {new_examples_count}\n"
                    f"- Optimizatsiya manbasi: ai_orders/ai_order_dataset jadvalidagi "
                    "oxirgi 300 ta zakaz.\n"
                    "- Asosiy maqsad: telefon raqam va summani aniqlashdagi xatolarni "
                    "kamaytirish, chalkash so'zlarni (masalan, 'to'qsonlik', 'birlik' va h.k.) "
                    "telefon/summa deb noto'g'ri ushlamaslik."
                )

                config_str = json.dumps(diff_payload, ensure_ascii=False, indent=2)
                if len(config_str) > 3500:
                    config_str_short = config_str[:3400] + "\n...\n(truncated)"
                else:
                    config_str_short = config_str

                text = (
                    "<b>🧠 Yangi prompt o'zgarishlari</b>\n"
                    "<i>(optimizer orqali yangilandi)</i>\n\n"
                    f"{reason_text}\n\n"
                    "<b>Diff (faqat yangi/yangilangan qism):</b>\n"
                    "<pre>" + html.escape(config_str_short) + "</pre>"
                )

                await message.bot.send_message(
                    chat_id=PROMPT_DEBUG_CHAT_ID,
                    text=text,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.exception(
                    "Yangi prompt diff ni guruhga yuborishda xatolik: %s", e
                )

        except Exception as e:
            logger.exception("Prompt optimizatsiyada xatolik: %s", e)
            short_err = str(e)
            if len(short_err) > 500:
                short_err = short_err[:500] + " ..."
            await message.answer(
                "❌ Prompt optimizatsiyada xatolik yuz berdi.\n"
                "Detal uchun server logini ko'ring.\n\n"
                f"{short_err}"
            )

    # =========================
    # 2) ACTIVE PROMPT'NI KO'RISH
    # =========================
    @dp.message(Command("prompt_show_active"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_prompt_show_active(message: Message):
        cfg = get_active_prompt_config(settings)
        if not cfg:
            await message.answer("Active prompt_config topilmadi (DB bo'sh).")
            return

        payload = _extract_payload(cfg)

        pretty = json.dumps(payload, ensure_ascii=False, indent=2)
        if len(pretty) > 3800:
            short = pretty[:3700] + "\n...\n(qisqartirildi)"
            await message.answer(
                f"<b>Active prompt_config (payload, short):</b>\n"
                f"<pre>{html.escape(short)}</pre>",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                f"<b>Active prompt_config (payload):</b>\n"
                f"<pre>{html.escape(pretty)}</pre>",
                parse_mode="HTML",
            )

    # =========================
    # 3) QO'LDA JSON PROMPT KIRITISH
    # =========================
    @dp.message(Command("prompt_set_manual"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_prompt_set_manual(message: Message):
        """
        Qo'lda JSON prompt o'rnatish.

        Variant A:
          1) JSON promptni xabar sifatida yuborasiz
          2) Unga reply qilib /prompt_set_manual yozasiz

        Variant B:
          /prompt_set_manual {json}
          bitta xabarning o'zida JSON yuborasiz.
        """
        raw_json: str | None = None

        # Variant A: reply qilingan xabarni tekshiramiz
        if message.reply_to_message and message.reply_to_message.text:
            raw_json = message.reply_to_message.text.strip()

        # Variant B: komandadan keyin matn bo'lsa
        elif message.text:
            parts = message.text.split(maxsplit=1)
            if len(parts) == 2:
                raw_json = parts[1].strip()

        if not raw_json:
            await message.answer(
                "JSON topilmadi.\n"
                "1) Avval JSON promptni alohida xabar sifatida yuboring, "
                "keyin unga reply qilib /prompt_set_manual yozing\n"
                "YOKI\n"
                "2) /prompt_set_manual {json} ko'rinishida yuboring."
            )
            return

        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as e:
            await message.answer(
                f"JSON xato: <code>{html.escape(str(e))}</code>",
                parse_mode="HTML",
            )
            return

        row = create_prompt_config(settings, payload, source="manual", make_active=True)

        await message.answer(
            "✅ Yangi prompt_config saqlandi va active qilindi.\n"
            f"ID: <b>{row['id']}</b>\n"
            f"Version: <b>{row['version']}</b>\n"
            f"Source: <b>{row['source']}</b>\n"
            f"Active: <b>{row['is_active']}</b>",
            parse_mode="HTML",
        )

    # =========================
    # 4) RULE QO'SHISH: /prompt_add_rule <section> <rule text>
    # =========================
    @dp.message(Command("prompt_add_rule"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_prompt_add_rule(message: Message):
        """
        Misol:
          /prompt_add_rule phones "00 lik hech qachon telefon raqam emas"
        """
        try:
            parts = message.text.split(" ", 2)
            if len(parts) < 3:
                await message.answer("Format: /prompt_add_rule <section> <rule text>")
                return

            section = parts[1].strip()
            rule_text = parts[2].strip()

            cfg = get_active_prompt_config(settings)
            if not cfg:
                await message.answer("Active prompt_config topilmadi.")
                return

            payload = _extract_payload(cfg)
            rules = payload.get("rules") or {}

            if section not in rules:
                await message.answer(f"'{section}' bo‘limi rules ichida topilmadi.")
                return

            if not isinstance(rules[section], list):
                await message.answer(
                    f"'{section}' bo‘limi list emas (rules[section] list bo‘lishi kerak)."
                )
                return

            rules[section].append(rule_text)

            saved = _save_new_prompt_version(
                settings=settings,
                payload=payload,
                source="manual_add_rule",
            )

            await message.answer(
                "✅ Qoida qo‘shildi.\n"
                f"Bo‘lim: <b>{section}</b>\n"
                f"Yangi version: <b>{saved['version']}</b>",
                parse_mode="HTML",
            )

        except Exception as e:
            logger.exception("prompt_add_rule xatolik: %s", e)
            await message.answer(f"Xatolik: {e}")

    # =========================
    # 5) RULE'LARNI KO'RISH: /prompt_list_rules <section>
    # =========================
    @dp.message(Command("prompt_list_rules"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_prompt_list_rules(message: Message):
        """
        Misol:
          /prompt_list_rules phones
        """
        parts = message.text.split(" ", 1)
        if len(parts) < 2:
            await message.answer("Format: /prompt_list_rules <section>")
            return

        section = parts[1].strip()

        cfg = get_active_prompt_config(settings)
        if not cfg:
            await message.answer("Active prompt_config topilmadi.")
            return

        payload = _extract_payload(cfg)
        rules = payload.get("rules") or {}

        if section not in rules:
            await message.answer(f"'{section}' bo‘limi rules ichida topilmadi.")
            return

        lst = rules[section]
        if not isinstance(lst, list):
            await message.answer(f"'{section}' bo‘limi list emas.")
            return

        if not lst:
            await message.answer(f"'{section}' bo‘limida hozircha qoida yo‘q.")
            return

        lines = [f"{idx}. {rule}" for idx, rule in enumerate(lst)]
        text = "\n".join(lines)

        await message.answer(
            f"<b>Bo‘lim:</b> {section}\n\n<pre>{html.escape(text)}</pre>",
            parse_mode="HTML",
        )

    # =========================
    # 6) RULE O'CHIRISH: /prompt_remove_rule <section> <index>
    # =========================
    @dp.message(Command("prompt_remove_rule"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_prompt_remove_rule(message: Message):
        """
        Misol:
          /prompt_remove_rule phones 4
        """
        parts = message.text.split(" ", 2)
        if len(parts) < 3:
            await message.answer("Format: /prompt_remove_rule <section> <index>")
            return

        section = parts[1].strip()
        try:
            idx = int(parts[2])
        except ValueError:
            await message.answer("Index butun son bo‘lishi kerak.")
            return

        cfg = get_active_prompt_config(settings)
        if not cfg:
            await message.answer("Active prompt_config topilmadi.")
            return

        payload = _extract_payload(cfg)
        rules = payload.get("rules") or {}

        if section not in rules:
            await message.answer(f"'{section}' bo‘limi rules ichida topilmadi.")
            return

        lst = rules[section]
        if not isinstance(lst, list):
            await message.answer(f"'{section}' bo‘limi list emas.")
            return

        if idx < 0 or idx >= len(lst):
            await message.answer("Index bo‘lim chegarasidan tashqarida.")
            return

        removed = lst.pop(idx)

        saved = _save_new_prompt_version(
            settings=settings,
            payload=payload,
            source="manual_remove_rule",
        )

        await message.answer(
            "❌ Qoida o‘chirildi.\n"
            f"Bo‘lim: <b>{section}</b>\n"
            f"Index: <b>{idx}</b>\n"
            f"Matn: <code>{html.escape(str(removed))}</code>\n\n"
            f"Yangi version: <b>{saved['version']}</b>",
            parse_mode="HTML",
        )
