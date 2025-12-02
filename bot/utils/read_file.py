# bot/utils/read_file.py
def read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "❗️Xatolik: a.txt faylini o‘qib bo‘lmadi."
