# bot/db.py
from typing import List, Optional

import psycopg2
from psycopg2.extras import Json
from aiogram.types import Message

from .config import Settings

_connection = None


def _get_connection(settings: Settings):
    """
    Bitta global connection. Autocommit yoqilgan.
    """
    global _connection
    if _connection is None or _connection.closed:
        if not settings.db_dsn:
            raise RuntimeError("DB_DSN .env ichida ko'rsatilmagan, Postgresga ulana olmayman.")
        _connection = psycopg2.connect(settings.db_dsn)
        _connection.autocommit = True
    return _connection


def init_db(settings: Settings) -> None:
    conn = _get_connection(settings)
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_orders (
                id              SERIAL PRIMARY KEY,
                user_message_id BIGINT,
                user_id         BIGINT NOT NULL,
                username        TEXT,
                full_name       TEXT,
                group_id        BIGINT NOT NULL,
                group_title     TEXT,
                order_text      TEXT,
                phones          TEXT[],
                location        JSONB,
                created_at      TIMESTAMPTZ DEFAULT now()
            );
            """
        )


def save_order_row(
    settings: Settings,
    *,
    message: Message,
    phones: Optional[List[str]],
    order_text: str,
    location: Optional[dict],
) -> None:
    """
    Zakaz finalize bo'lganda DB ga yozish.
    """
    conn = _get_connection(settings)
    user = message.from_user

    username = user.username if user and user.username else None
    full_name = user.full_name if user and user.full_name else None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_orders (
                user_message_id,
                user_id,
                username,
                full_name,
                group_id,
                group_title,
                order_text,
                phones,
                location
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                message.message_id,
                user.id if user else None,
                username,
                full_name,
                message.chat.id,
                message.chat.title,
                order_text,
                phones if phones else None,
                Json(location) if location else None,
            ),
        )
