# bot/ai/stt_uzbekvoice.py
import asyncio
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

UZBEKVOICE_STT_URL = "https://uzbekvoice.ai/api/v1/stt"


def _stt_sync(
        file_bytes: bytes,
        api_key: str,
        language: str = "uz",
) -> Optional[str]:
    headers = {
        "Authorization": api_key,
    }

    files = {
        "file": ("voice.ogg", file_bytes, "audio/ogg"),
    }

    data = {
        "return_offsets": "false",
        "run_diarization": "false",
        "language": language,
        "blocking": "true",
    }

    resp = requests.post(
        UZBEKVOICE_STT_URL,
        headers=headers,
        files=files,
        data=data,
        timeout=60,
    )
    resp.raise_for_status()
    j = resp.json()
    logger.debug("Uzbekvoice response: %s", j)

    if isinstance(j, dict):
        if "text" in j:
            return j["text"]
        if "result" in j and isinstance(j["result"], dict):
            return j["result"].get("text")

    return None


async def stt_uzbekvoice(
        file_bytes: bytes,
        api_key: str,
        language: str = "uz",
) -> Optional[str]:
    return await asyncio.to_thread(_stt_sync, file_bytes, api_key, language)
