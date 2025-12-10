# bot/ai/prompt_manager.py
import json
import hashlib
from pathlib import Path
from typing import Any, Dict, Tuple

CONFIG_PATH = Path(__file__).resolve().parent / "prompt_config.json"
BACKUP_DIR = Path(__file__).resolve().parent / "prompt_backups"


def load_prompt_config() -> Tuple[Dict[str, Any], str]:
    """
    prompt_config.json ni o'qiydi va (config, config_hash) qaytaradi
    """
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
    config_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return data, config_hash


def save_prompt_config(new_config: Dict[str, Any]) -> None:
    """
    Yangi konfiguratsiyani saqlaydi va eski versiyani backup qiladi
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    if CONFIG_PATH.exists():
        old_content = CONFIG_PATH.read_text(encoding="utf-8")
        backup_path = BACKUP_DIR / "prompt_config_backup.jsonl"
        with open(backup_path, "a", encoding="utf-8") as bf:
            bf.write(old_content + "\n---\n")

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(new_config, f, ensure_ascii=False, indent=2)
