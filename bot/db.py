# bot/db.py
from typing import List, Optional

import psycopg2
from aiogram.types import Message
from psycopg2.extras import Json

from .config import Settings

_connection = None


def _get_connection(settings: Settings):
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
                is_active       BOOLEAN NOT NULL DEFAULT TRUE,
                cancelled_at    TIMESTAMPTZ,
                created_at      TIMESTAMPTZ DEFAULT now()
            );
            """
        )
        # Agar eski jadval bo'lsa, ustunlarni ALter orqali qo'shik (xavfsiz varianti)
        cur.execute(
            """
            ALTER TABLE ai_orders
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
            """
        )
        cur.execute(
            """
            ALTER TABLE ai_orders
            ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;
            """
        )


def save_order_row(
        settings: Settings,
        *,
        message: Message,
        phones: Optional[List[str]],
        order_text: str,
        location: Optional[dict],
) -> int:
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
                location,
                is_active,
                cancelled_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, NULL)
            RETURNING id;
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
        new_id_row = cur.fetchone()
        order_id = new_id_row[0]
        return order_id


def cancel_order_row(settings: Settings, order_id: int) -> bool:
    conn = _get_connection(settings)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ai_orders
            SET is_active = FALSE,
                cancelled_at = NOW()
            WHERE id = %s
              AND is_active = TRUE;
            """,
            (order_id,),
        )
        return cur.rowcount > 0
