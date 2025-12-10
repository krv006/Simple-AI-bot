# bot/db.py
from typing import List, Optional

import psycopg2
from aiogram.types import Message
from psycopg2.extras import Json

from .config import Settings

_connection = None

import json  # tepada bo'lmasa, qo'shing


def load_orders_for_prompt_dataset(
        settings: Settings,
        limit: int = 200,
):
    """
    prompt optimizer uchun ai_orders jadvalidan so'nggi N ta yozuvni olib beradi.
    Qaytadi: List[dict] with keys:
      - raw_text
      - true_phones
      - true_amount
      - true_address
    """
    conn = _get_connection(settings)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                order_text,
                phones,
                amount,
                location
            FROM ai_orders
            WHERE order_text IS NOT NULL
            ORDER BY created_at DESC
            LIMIT %s;
            """,
            (limit,),
        )
        rows = cur.fetchall()

    records = []
    for order_text, phones, amount, location in rows:
        if not order_text:
            continue

        true_phones = phones or []
        true_amount = int(amount) if amount is not None else None

        true_address = None
        if location is not None:
            if isinstance(location, dict):
                true_address = (
                        location.get("address")
                        or location.get("raw")
                        or None
                )
            else:
                try:
                    loc_obj = json.loads(location)
                    true_address = (
                            loc_obj.get("address")
                            or loc_obj.get("raw")
                            or None
                    )
                except Exception:
                    true_address = None

        records.append(
            {
                "raw_text": order_text,
                "true_phones": true_phones,
                "true_amount": true_amount,
                "true_address": true_address,
            }
        )

    return records


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
                amount          BIGINT,
                is_active       BOOLEAN NOT NULL DEFAULT TRUE,
                cancelled_at    TIMESTAMPTZ,
                created_at      TIMESTAMPTZ DEFAULT now()
            );
            """
        )
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
        cur.execute(
            """
            ALTER TABLE ai_orders
            ADD COLUMN IF NOT EXISTS amount BIGINT;
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_voice_logs (
                id              SERIAL PRIMARY KEY,
                user_message_id BIGINT,
                user_id         BIGINT NOT NULL,
                username        TEXT,
                full_name       TEXT,
                group_id        BIGINT NOT NULL,
                group_title     TEXT,
                voice_file_id   TEXT,
                stt_text        TEXT,
                phones          TEXT[],
                amount          BIGINT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_check_logs (
                id              SERIAL PRIMARY KEY,
                user_message_id BIGINT,
                user_id         BIGINT,
                username        TEXT,
                full_name       TEXT,
                group_id        BIGINT,
                group_title     TEXT,
                text            TEXT,
                ai              JSONB,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_error_logs (
                id              SERIAL PRIMARY KEY,
                user_message_id BIGINT,
                user_id         BIGINT,
                username        TEXT,
                full_name       TEXT,
                group_id        BIGINT,
                group_title     TEXT,
                text            TEXT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
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
        amount: Optional[int] = None,
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
                amount,
                is_active,
                cancelled_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, NULL)
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
                amount,
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


def update_order_row(
        settings: Settings,
        order_id: int,
        *,
        phones: Optional[List[str]],
        order_text: str,
        location: Optional[dict],
        amount: Optional[int],
) -> bool:
    """
    Mavjud ai_orders yozuvini yangilash.
    order_id bo'yicha:
      - phones
      - order_text
      - location
      - amount
    ustunlari update qilinadi (faqat is_active = TRUE bo'lsa).
    """
    conn = _get_connection(settings)

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ai_orders
            SET
                phones     = %s,
                order_text = %s,
                location   = %s,
                amount     = %s
            WHERE id = %s
              AND is_active = TRUE;
            """,
            (
                phones if phones else None,
                order_text,
                Json(location) if location else None,
                amount,
                order_id,
            ),
        )
        return cur.rowcount > 0


def save_voice_stt_row(
        settings: Settings,
        *,
        message: Message,
        text: str,
        phones: Optional[List[str]] = None,
        amount: Optional[int] = None,
) -> int:
    conn = _get_connection(settings)
    user = message.from_user

    username = user.username if user and user.username else None
    full_name = user.full_name if user and user.full_name else None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_voice_logs (
                user_message_id,
                user_id,
                username,
                full_name,
                group_id,
                group_title,
                voice_file_id,
                stt_text,
                phones,
                amount
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                message.message_id,
                user.id if user else None,
                username,
                full_name,
                message.chat.id,
                message.chat.title,
                message.voice.file_id if message.voice else None,
                text,
                phones if phones else None,
                amount,
            ),
        )
        new_id_row = cur.fetchone()
        voice_id = new_id_row[0]
        return voice_id


def save_ai_check_row(
        settings: Settings,
        *,
        message: Message,
        text: str,
        ai_result: dict,
) -> int:
    conn = _get_connection(settings)
    user = message.from_user

    username = user.username if user and user.username else None
    full_name = user.full_name if user and user.full_name else None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_check_logs (
                user_message_id,
                user_id,
                username,
                full_name,
                group_id,
                group_title,
                text,
                ai
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                message.message_id,
                user.id if user else None,
                username,
                full_name,
                message.chat.id,
                message.chat.title,
                text,
                Json(ai_result) if ai_result is not None else None,
            ),
        )
        row = cur.fetchone()
        return row[0]


def save_error_row(
        settings: Settings,
        *,
        message: Message,
        text: str,
) -> int:
    conn = _get_connection(settings)
    user = message.from_user

    username = user.username if user and user.username else None
    full_name = user.full_name if user and user.full_name else None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_error_logs (
                user_message_id,
                user_id,
                username,
                full_name,
                group_id,
                group_title,
                text
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                message.message_id,
                user.id if user else None,
                username,
                full_name,
                message.chat.id,
                message.chat.title,
                text,
            ),
        )
        row = cur.fetchone()
        return row[0]
