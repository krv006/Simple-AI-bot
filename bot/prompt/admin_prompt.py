# bot/handlers/admin_prompt.py
import copy
import html
import json
import logging
from typing import Any, Dict, List, Set

from aiogram import Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.filters.callback_data import CallbackData

from bot.config import Settings
from bot.db import (
    create_prompt_config,
    get_active_prompt_config,
)
from bot.prompt.prompt_optimizer import optimize_prompt_from_dataset

logger = logging.getLogger(__name__)

ADMIN_IDS = {1305675046, 120147430}
PROMPT_DEBUG_CHAT_ID = -5030824970

PROMPT_RULE_SECTIONS = [
    "general",
    "phones",
    "amount",
    "address",
    "comments",
    "updates",
    "confidence",
]


# ================
# Inline callback data
# ================
class PromptRuleCB(CallbackData, prefix="prule"):
    action: str   # choose_section | toggle_optimize | cancel
    section: str  # phones | amount | ...
    opt: str      # "0" | "1"


# ================
# FSM state
# ================
class PromptRuleState(StatesGroup):
    waiting_rule_text = State()


def _build_prompt_diff_payload(
    old_config: Dict[str, Any],
    new_config: Dict[str, Any],
) -> Dict[str, Any]:
    old_rules = (old_config.get("rules") or {}) if isinstance(old_config, dict) else {}
    new_rules = (new_config.get("rules") or {}) if isinstance(new_config, dict) else {}

    changed_rules: Dict[str, Dict[str, List[str]]] = {}
    for section, new_list in new_rules.items():
        old_list = old_rules.get(section, [])
        if new_list != old_list:
            changed_rules[section] = {"old": old_list, "new": new_list}

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

    return {"changed_rules": changed_rules, "new_examples": new_examples_only}


def _extract_payload(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    get_active_prompt_config 2 xil bo'lishi mumkin:
      1) payloadning o'zi
      2) {"id":..., "payload": {...}, "version":...}
    """
    if not isinstance(cfg, dict):
        return {}
    inner = cfg.get("payload")
    if isinstance(inner, dict):
        return inner
    return cfg


def _save_new_prompt_version(
    settings: Settings,
    payload: Dict[str, Any],
    source: str = "manual_update",
) -> Dict[str, Any]:
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
        row.get("id"),
        row.get("version"),
        row.get("source"),
    )
    return row


def _kb_sections(optimize_after: bool = False) -> InlineKeyboardMarkup:
    opt = "1" if optimize_after else "0"
    rows: List[List[InlineKeyboardButton]] = []

    buf: List[InlineKeyboardButton] = []
    for sec in PROMPT_RULE_SECTIONS:
        buf.append(
            InlineKeyboardButton(
                text=sec,
                callback_data=PromptRuleCB(action="choose_section", section=sec, opt=opt).pack(),
            )
        )
        if len(buf) == 2:
            rows.append(buf)
            buf = []
    if buf:
        rows.append(buf)

    rows.append(
        [
            InlineKeyboardButton(
                text=f"Optimize: {'ON' if optimize_after else 'OFF'}",
                callback_data=PromptRuleCB(action="toggle_optimize", section="_", opt=opt).pack(),
            ),
            InlineKeyboardButton(
                text="Bekor qilish",
                callback_data=PromptRuleCB(action="cancel", section="_", opt=opt).pack(),
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _is_plain_command(message: Message, cmd: str) -> bool:
    """
    /cmd yoki /cmd@botusername holatlarini aniqlaydi (argumentsiz).
    """
    if not message.text:
        return False
    t = message.text.strip()
    if t == f"/{cmd}":
        return True
    # /cmd@botname
    if message.bot and message.bot.username and t == f"/{cmd}@{message.bot.username}":
        return True
    return False


def register_admin_prompt_handlers(dp: Dispatcher, settings: Settings) -> None:
    # =========================
    # 1) OPTIMIZER ORQALI YANGILASH
    # =========================
    @dp.message(Command("optimize_prompt"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_optimize_prompt(message: Message):
        await message.answer("♻️ Prompt optimizatsiya qilinyapti...")

        try:
            result = optimize_prompt_from_dataset(settings=settings, limit=300)
            old_config = result.get("old_config") or {}
            new_config = result.get("new_config") or {}

            row = create_prompt_config(
                settings=settings,
                payload=new_config,
                source="optimizer",
                make_active=True,
            )

            await message.answer(
                "✅ prompt_config yangilandi (optimizer) va DB'da active qilindi.\n"
                f"ID: <b>{row.get('id')}</b> | Version: <b>{row.get('version')}</b>",
                parse_mode=ParseMode.HTML,
            )

            # Debug diff
            try:
                diff_payload = _build_prompt_diff_payload(old_config, new_config)
                changed_rules = diff_payload["changed_rules"]
                new_examples = diff_payload["new_examples"]

                if not changed_rules and not new_examples:
                    await message.bot.send_message(
                        chat_id=PROMPT_DEBUG_CHAT_ID,
                        text="ℹ️ /optimize_prompt ishga tushdi, lekin sezilarli o'zgarish yo'q.",
                    )
                    return

                changed_sections_str = ", ".join(changed_rules.keys()) if changed_rules else "—"
                reason_text = (
                    "<b>Qisqa izoh:</b>\n"
                    f"- O'zgargan rules bo'limlari: {html.escape(changed_sections_str)}\n"
                    f"- Yangi misollar soni: {len(new_examples)}\n"
                    "- Maqsad: xatolarni kamaytirish."
                )

                config_str = json.dumps(diff_payload, ensure_ascii=False, indent=2)
                if len(config_str) > 3500:
                    config_str = config_str[:3400] + "\n...\n(truncated)"

                text = (
                    "<b>🧠 Yangi prompt o'zgarishlari</b>\n"
                    "<i>(optimizer orqali)</i>\n\n"
                    f"{reason_text}\n\n"
                    "<b>Diff:</b>\n"
                    "<pre>" + html.escape(config_str) + "</pre>"
                )

                await message.bot.send_message(
                    chat_id=PROMPT_DEBUG_CHAT_ID,
                    text=text,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.exception("Diff yuborishda xatolik: %s", e)

        except Exception as e:
            logger.exception("Prompt optimizatsiyada xatolik: %s", e)
            short_err = str(e)[:500]
            await message.answer(
                "❌ Prompt optimizatsiyada xatolik.\n"
                f"<code>{html.escape(short_err)}</code>",
                parse_mode=ParseMode.HTML,
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
            pretty = pretty[:3700] + "\n...\n(qisqartirildi)"

        await message.answer(
            f"<b>Active prompt_config (payload):</b>\n<pre>{html.escape(pretty)}</pre>",
            parse_mode=ParseMode.HTML,
        )

    # =========================
    # 3) QO'LDA JSON PROMPT KIRITISH
    # =========================
    @dp.message(Command("prompt_set_manual"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_prompt_set_manual(message: Message):
        raw_json: str | None = None

        if message.reply_to_message and message.reply_to_message.text:
            raw_json = message.reply_to_message.text.strip()
        elif message.text:
            parts = message.text.split(maxsplit=1)
            if len(parts) == 2:
                raw_json = parts[1].strip()

        if not raw_json:
            await message.answer(
                "JSON topilmadi.\n"
                "1) JSON ni alohida xabar qilib yuboring, keyin reply qilib /prompt_set_manual\n"
                "YOKI\n"
                "2) /prompt_set_manual {json}"
            )
            return

        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as e:
            await message.answer(
                f"JSON xato: <code>{html.escape(str(e))}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        row = create_prompt_config(settings, payload, source="manual", make_active=True)
        await message.answer(
            "✅ Yangi prompt_config saqlandi va active qilindi.\n"
            f"ID: <b>{row.get('id')}</b>\n"
            f"Version: <b>{row.get('version')}</b>\n"
            f"Source: <b>{row.get('source')}</b>",
            parse_mode=ParseMode.HTML,
        )

    # =========================
    # 4) /prompt_add_rule
    #    - argumentsiz -> INLINE section tanlash
    #    - argumentli  -> classic qo'shish
    # =========================
    @dp.message(Command("prompt_add_rule"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_prompt_add_rule(message: Message, state: FSMContext):
        # A) argumentsiz bo'lsa -> inline menyu
        if _is_plain_command(message, "prompt_add_rule"):
            await state.clear()
            await message.answer(
                "Qaysi bo‘limga qoida qo‘shamiz? Tanlang:",
                reply_markup=_kb_sections(optimize_after=False),
            )
            return

        # B) classic: /prompt_add_rule <section> <rule text>
        try:
            parts = (message.text or "").split(" ", 2)
            if len(parts) < 3:
                await message.answer("Format: /prompt_add_rule &lt;section&gt; &lt;rule text&gt;")
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
                await message.answer(f"'{html.escape(section)}' bo‘limi rules ichida topilmadi.")
                return
            if not isinstance(rules[section], list):
                await message.answer(f"'{html.escape(section)}' bo‘limi list emas.")
                return

            if rule_text in rules[section]:
                await message.answer("Bu qoida allaqachon mavjud.")
                return

            rules[section].append(rule_text)

            saved = _save_new_prompt_version(
                settings=settings,
                payload=payload,
                source=f"manual_add_rule:{section}",
            )

            await message.answer(
                "✅ Qoida qo‘shildi.\n"
                f"Bo‘lim: <b>{html.escape(section)}</b>\n"
                f"Yangi version: <b>{saved.get('version')}</b>",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.exception("prompt_add_rule xatolik: %s", e)
            await message.answer(f"Xatolik: {e}")

    # =========================
    # 5) RULE'LARNI KO'RISH: /prompt_list_rules <section>
    # =========================
    @dp.message(Command("prompt_list_rules"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_prompt_list_rules(message: Message):
        parts = (message.text or "").split(" ", 1)
        if len(parts) < 2:
            await message.answer("Format: /prompt_list_rules &lt;section&gt;", parse_mode=ParseMode.HTML)
            return

        section = parts[1].strip()

        cfg = get_active_prompt_config(settings)
        if not cfg:
            await message.answer("Active prompt_config topilmadi.")
            return

        payload = _extract_payload(cfg)
        rules = payload.get("rules") or {}

        if section not in rules:
            await message.answer(f"'{html.escape(section)}' bo‘limi rules ichida topilmadi.", parse_mode=ParseMode.HTML)
            return

        lst = rules[section]
        if not isinstance(lst, list):
            await message.answer(f"'{html.escape(section)}' bo‘limi list emas.", parse_mode=ParseMode.HTML)
            return

        if not lst:
            await message.answer(f"'{html.escape(section)}' bo‘limida hozircha qoida yo‘q.", parse_mode=ParseMode.HTML)
            return

        lines = [f"{idx}. {rule}" for idx, rule in enumerate(lst)]
        text = "\n".join(lines)

        await message.answer(
            f"<b>Bo‘lim:</b> {html.escape(section)}\n\n<pre>{html.escape(text)}</pre>",
            parse_mode=ParseMode.HTML,
        )

    # =========================
    # 6) RULE O'CHIRISH: /prompt_remove_rule <section> <index>
    # =========================
    @dp.message(Command("prompt_remove_rule"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_prompt_remove_rule(message: Message):
        parts = (message.text or "").split(" ", 2)
        if len(parts) < 3:
            await message.answer("Format: /prompt_remove_rule &lt;section&gt; &lt;index&gt;", parse_mode=ParseMode.HTML)
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
            await message.answer(f"'{html.escape(section)}' bo‘limi rules ichida topilmadi.", parse_mode=ParseMode.HTML)
            return

        lst = rules[section]
        if not isinstance(lst, list):
            await message.answer(f"'{html.escape(section)}' bo‘limi list emas.", parse_mode=ParseMode.HTML)
            return

        if idx < 0 or idx >= len(lst):
            await message.answer("Index bo‘lim chegarasidan tashqarida.")
            return

        removed = lst.pop(idx)

        saved = _save_new_prompt_version(
            settings=settings,
            payload=payload,
            source=f"manual_remove_rule:{section}",
        )

        await message.answer(
            "❌ Qoida o‘chirildi.\n"
            f"Bo‘lim: <b>{html.escape(section)}</b>\n"
            f"Index: <b>{idx}</b>\n"
            f"Matn: <code>{html.escape(str(removed))}</code>\n\n"
            f"Yangi version: <b>{saved.get('version')}</b>",
            parse_mode=ParseMode.HTML,
        )

    # =========================
    # 7) INLINE FLOW alias: /prompt_rule_add (xohlasangiz qolsin)
    # =========================
    @dp.message(Command("prompt_rule_add"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_prompt_rule_add(message: Message, state: FSMContext):
        await state.clear()
        await message.answer(
            "Qaysi bo‘limga qoida qo‘shamiz? Tanlang:",
            reply_markup=_kb_sections(optimize_after=False),
        )

    # Toggle optimize
    @dp.callback_query(PromptRuleCB.filter(F.action == "toggle_optimize"), F.from_user.id.in_(ADMIN_IDS))
    async def cb_toggle_optimize(query: CallbackQuery, callback_data: PromptRuleCB, state: FSMContext):
        new_opt = "0" if callback_data.opt == "1" else "1"
        await query.message.edit_reply_markup(reply_markup=_kb_sections(optimize_after=(new_opt == "1")))
        await query.answer("OK")

    # Cancel
    @dp.callback_query(PromptRuleCB.filter(F.action == "cancel"), F.from_user.id.in_(ADMIN_IDS))
    async def cb_cancel(query: CallbackQuery, callback_data: PromptRuleCB, state: FSMContext):
        await state.clear()
        await query.message.edit_text("Bekor qilindi.")
        await query.answer("OK")

    # Choose section
    @dp.callback_query(PromptRuleCB.filter(F.action == "choose_section"), F.from_user.id.in_(ADMIN_IDS))
    async def cb_choose_section(query: CallbackQuery, callback_data: PromptRuleCB, state: FSMContext):
        section = callback_data.section
        optimize_after = (callback_data.opt == "1")

        await state.set_state(PromptRuleState.waiting_rule_text)
        await state.update_data(section=section, optimize_after=optimize_after)

        await query.message.edit_text(
            f"Bo‘lim: <b>{html.escape(section)}</b>\n\n"
            "Endi qo‘shiladigan qoida matnini yuboring.\n"
            "Masalan: <code>00 lik hech qachon telefon raqam emas</code>\n\n"
            f"Auto-optimize: <b>{'ON' if optimize_after else 'OFF'}</b>",
            parse_mode=ParseMode.HTML,
        )
        await query.answer("OK")

    # Receive rule text
    @dp.message(PromptRuleState.waiting_rule_text, F.from_user.id.in_(ADMIN_IDS))
    async def st_rule_text(message: Message, state: FSMContext):
        data = await state.get_data()
        section = data.get("section")
        optimize_after = bool(data.get("optimize_after"))

        rule_text = (message.text or "").strip()
        if not rule_text:
            await message.answer("Bo‘sh matn bo‘lmaydi. Qoidani matn sifatida yuboring.")
            return

        cfg = get_active_prompt_config(settings)
        if not cfg:
            await message.answer("Active prompt topilmadi. Avval seed/manual/optimizer bilan prompt yarating.")
            await state.clear()
            return

        payload = _extract_payload(cfg)
        rules = payload.get("rules") or {}

        if section not in rules or not isinstance(rules.get(section), list):
            await message.answer(
                f"'{html.escape(str(section))}' bo‘limi rules ichida topilmadi yoki list emas.\n"
                "Avval /prompt_show_active qilib tekshiring.",
                parse_mode=ParseMode.HTML,
            )
            await state.clear()
            return

        if rule_text in rules[section]:
            await message.answer("Bu qoida allaqachon mavjud. Yangi qoida yozing.")
            return

        rules[section].append(rule_text)

        saved = _save_new_prompt_version(
            settings=settings,
            payload=payload,
            source=f"inline_add_rule:{section}",
        )

        await message.answer(
            "✅ Qoida qo‘shildi va yangi version active qilindi.\n"
            f"Bo‘lim: <b>{html.escape(section)}</b>\n"
            f"Version: <b>{saved.get('version')}</b>\n"
            f"Auto-optimize: <b>{'ON' if optimize_after else 'OFF'}</b>",
            parse_mode=ParseMode.HTML,
        )

        await state.clear()

        # Auto optimize (ixtiyoriy)
        if optimize_after:
            try:
                await message.answer("♻️ Auto optimize ishga tushdi...")
                result = optimize_prompt_from_dataset(settings=settings, limit=300)
                new_config = result.get("new_config") or {}

                row = create_prompt_config(
                    settings=settings,
                    payload=new_config,
                    source="optimizer(auto_after_inline_rule_add)",
                    make_active=True,
                )

                await message.answer(
                    f"✅ Optimize tugadi. Yangi active version: <b>{row.get('version')}</b>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.exception("Auto optimize error: %s", e)
                await message.answer(f"❌ Optimize xato: {e}")

