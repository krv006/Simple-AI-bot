# bot/handlers/admin_prompt.py
from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from ..ai.prompt_optimizer_from_dataset import optimize_prompt_from_dataset
from ..config import Settings

ADMIN_IDS = {1305675046}


def register_admin_prompt_handlers(dp: Dispatcher, settings: Settings) -> None:
    @dp.message(Command("optimize_prompt"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_optimize_prompt(message: Message):
        await message.answer("♻️ Prompt optimizatsiya qilinyapti (DB asosida)...")
        try:
            optimize_prompt_from_dataset(
                settings=settings,
                limit=300,  # oxirgi 300 ta order asosida
            )
            await message.answer("✅ prompt_config.json yangilandi (DB asosida).")
        except Exception as e:
            await message.answer(f"❌ Xatolik: {e}")
