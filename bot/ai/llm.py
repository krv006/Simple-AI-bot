# bot/ai/llm.py
import json

from openai import OpenAI

from bot.config import Settings


def call_llm_as_json(settings: Settings, system_prompt: str, user_prompt: str) -> dict:
    client = OpenAI(api_key=settings.openai_api_key)

    resp = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
    )

    content = resp.choices[0].message.content

    try:
        return json.loads(content)
    except Exception as e:
        raise RuntimeError(f"LLM JSON qaytarmadi: {e}\n----\n{content}")
