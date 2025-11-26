# bot/storage.py
import json
import os
from datetime import datetime, timezone
from typing import Dict, Tuple, Optional

from aiogram.types import Message

from .config import Settings
from .models import OrderSession

SESSIONS: Dict[Tuple[int, int], OrderSession] = {}

LOG_FILE = "ai_bot.json"


def get_session_key(message: Message) -> Tuple[int, int]:
    return message.chat.id, message.from_user.id  # type: ignore[union-attr]


def get_or_create_session(settings: Settings, message: Message) -> OrderSession:
    from datetime import datetime, timezone

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
    if session.is_completed:
        return None
    session.is_completed = True
    return session


def clear_session(key: Tuple[int, int]) -> None:
    if key in SESSIONS:
        del SESSIONS[key]


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

    data = []

    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()

        if content:
            try:
                existing = json.loads(content)
                if isinstance(existing, list):
                    data = existing
                else:
                    data = [existing]
            except json.JSONDecodeError:
                lines = content.splitlines()
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        data.append(obj)
                    except json.JSONDecodeError:
                        continue

    data.append(log_entry)

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
