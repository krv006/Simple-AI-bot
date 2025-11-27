# main.py
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import load_settings
from bot.db import init_db
from bot.handlers.orders import register_order_handlers


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def main():
    settings = load_settings()

    if settings.db_dsn:
        init_db(settings)

    bot = Bot(
        token=settings.tg_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()

    register_order_handlers(dp, settings)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
