# bot/config.py
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Settings:
    tg_bot_token: str
    openai_api_key: str | None
    openai_model: str
    gemini_api_key: str | None
    gemini_model: str
    max_diff_seconds: int
    geocoder_user_agent: str
    debug: bool
    send_group_id: int | None  # SEND_GROUP_ID dan keladi

    @property
    def openai_enabled(self) -> bool:
        """OpenAI ishlatish mumkinmi-yo'qmi (kalit bor-yo'qligiga qarab)."""
        return bool(self.openai_api_key)


def load_settings() -> Settings:
    load_dotenv()

    tg_bot_token = os.getenv("TG_BOT_TOKEN")
    if not tg_bot_token:
        raise RuntimeError("TG_BOT_TOKEN .env ichida ko'rsatilmagan!")

    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    max_diff_seconds = int(os.getenv("MAX_DIFF_SECONDS", "120"))
    geocoder_user_agent = os.getenv("GEOCODER_USER_AGENT", "ai_taxi_bot")
    debug = os.getenv("DEBUG", "False").lower() == "true"

    # SEND_GROUP_ID ni o'qiymiz
    send_group_raw = os.getenv("SEND_GROUP_ID")
    send_group_id: int | None = None
    if send_group_raw:
        try:
            send_group_id = int(send_group_raw)
        except ValueError:
            send_group_id = None

    return Settings(
        tg_bot_token=tg_bot_token,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        max_diff_seconds=max_diff_seconds,
        geocoder_user_agent=geocoder_user_agent,
        debug=debug,
        send_group_id=send_group_id,
    )
