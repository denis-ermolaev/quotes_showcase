import json

TOTAL = 340_000
TARGET_BYTES = 110 * 1024 * 1024  # ~110 МБ

AUTHORS = [
    "Марк Аврелий",
    "Сенека",
    "Эпиктет",
    "Зенон Китийский",
    "Клеанф",
    "Хрисипп",
    "Панэций",
    "Посидоний",
]

TAGS_POOL = [
    ["стоицизм"],
    ["стоицизм", "философия"],
    ["философия"],
    ["этика"],
    ["мудрость"],
]

# Слова для генерации более-менее осмысленного текста
WORDS = [
    "жизнь",
    "путь",
    "мысль",
    "время",
    "душа",
    "разум",
    "судьба",
    "воля",
    "добродетель",
    "мудрость",
    "спокойствие",
    "свобода",
    "счастье",
    "страдание",
    "природа",
    "долг",
    "истина",
    "благо",
]


def make_text(i: int, target_len: int) -> str:
    """Псевдослучайный текст из слов, длина ~target_len."""
    # Детерминированный «рандом» без random.seed — просто по индексу
    parts = []
    length = 0
    idx = i
    while length < target_len:
        w = WORDS[idx % len(WORDS)]
        parts.append(w)
        length += len(w) + 1
        idx = idx * 1103515245 + 12345  # LCG, чтобы не повторялось
        idx = (idx >> 16) & 0x7FFF
    text = " ".join(parts)
    return text[:target_len]


def make_quote(i: int, text_len: int) -> dict:
    return {
        "id": f"q-{i:07d}",
        "author": AUTHORS[i % len(AUTHORS)],
        "text": make_text(i, text_len),
        "tags": TAGS_POOL[i % len(TAGS_POOL)],
        "year": 170 + (i % 50),
        "lang": "ru",
    }


def estimate_avg_record(text_len: int) -> int:
    """Прикидка размера одной записи в байтах."""
    q = make_quote(1, text_len)
    return len(json.dumps(q, ensure_ascii=False).encode("utf-8"))


def main():
    # Подбираем text_len так, чтобы уложиться в ~110 МБ
    # Начнём с 100 и подкрутим
    text_len = 100
    avg = estimate_avg_record(text_len)
    # Хотим TOTAL * avg ≈ TARGET_BYTES
    target_avg = TARGET_BYTES // TOTAL
    text_len = max(1, text_len + (target_avg - avg))
    # На всякий случай подкрутим ещё раз
    avg = estimate_avg_record(text_len)
    text_len = max(1, text_len + (target_avg - avg))

    print(f"text_len={text_len}, avg_record≈{estimate_avg_record(text_len)} байт")

    written = 0
    with open("big_quotes.json", "w", encoding="utf-8") as f:
        f.write('{"quotes": [\n')
        for i in range(1, TOTAL + 1):
            q = make_quote(i, text_len)
            chunk = json.dumps(q, ensure_ascii=False)
            if i > 1:
                f.write(",\n")
            f.write(chunk)
            written = f.tell()
        f.write("\n]}")
    print(f"quotes: {TOTAL}, bytes: {written}, MB: {written / 1024 / 1024:.1f}")


if __name__ == "__main__":
    main()
