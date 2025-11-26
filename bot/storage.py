# bot/storage.py
import json
from datetime import datetime, timezone
from typing import Dict, Tuple, Optional

from aiogram.types import Message

from .config import Settings
from .models import OrderSession

SESSIONS: Dict[Tuple[int, int], OrderSession] = {}


def get_session_key(message: Message) -> Tuple[int, int]:
    return message.chat.id, message.from_user.id  # type: ignore[union-attr]


def get_or_create_session(settings: Settings, message: Message) -> OrderSession:
    key = get_session_key(message)
    now = datetime.now(timezone.utc)
    session = SESSIONS.get(key)

    if session:
        if (now - session.updated_at).total_seconds() > settings.max_diff_seconds:
            SESSIONS[key] = OrderSession(
                user_id=message.from_user.id,  # type: ignore[union-attr]
                chat_id=message.chat.id,
            )
    else:
        SESSIONS[key] = OrderSession(
            user_id=message.from_user.id,  # type: ignore[union-attr]
            chat_id=message.chat.id,
        )

    return SESSIONS[key]


def is_session_ready(session: OrderSession) -> bool:
    return bool(session.phones and session.location)


def finalize_session(key: Tuple[int, int]) -> Optional[OrderSession]:
    session = SESSIONS.get(key)
    if not session:
        return None
    session.is_completed = True
    return session


def clear_session(key: Tuple[int, int]) -> None:
    if key in SESSIONS:
        del SESSIONS[key]


LOG_FILE = "ai_bot.json"


def save_order_to_json(order: OrderSession) -> None:
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chat_id": order.chat_id,
        "user_id": order.user_id,
        "phones": list(order.phones),
        "location": order.location,
        "comments": order.comments,
        "product_texts": order.product_texts,
        "raw_messages": order.raw_messages,
    }

    # Write as NDJSON (newline-delimited JSON)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
